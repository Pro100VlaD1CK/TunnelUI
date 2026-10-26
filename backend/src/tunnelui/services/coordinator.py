import hashlib
import json
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.trusttunnel import (
    Credential,
    parse_credentials,
    preview,
    render_credentials,
    source_hash,
)
from tunnelui.models import Attachment, Client, Inbound, Operation, now
from tunnelui.security import SecretBox
from tunnelui.services.audit import record_result
from tunnelui.services.operations import (
    INCOMPLETE_STATES,
    InboundLocks,
    active_operation,
    hashes,
    operation_output,
    transition,
)
from tunnelui.system.files import atomic_replace, commit_atomic, prepare_atomic, private_backup
from tunnelui.system.provider import FakeSystemProvider, SystemOperationError, SystemProvider


class SimulatedCrash(BaseException):
    """Test-only power-loss signal that deliberately bypasses workflow exception handling."""


class ManagedEnvironment(Protocol):
    registry_id: str
    provider: SystemProvider
    version: str
    public_address: str

    def snapshot(self) -> dict[str, bytes]: ...
    def adoption_metadata(self, snapshot: dict[str, bytes]) -> tuple[dict, str, str]: ...
    def operation(self, operation_id: str): ...
    def create_backup(self, operation_id: str, snapshot: dict[str, bytes]) -> None: ...
    def prepare_credentials(self, operation_id: str, content: bytes) -> None: ...
    def commit_credentials(self, operation_id: str, expected_hashes: dict[str, str]) -> None: ...
    def restore_credentials(self, operation_id: str) -> None: ...
    def cleanup(self, operation_id: str) -> None: ...


@dataclass(frozen=True)
class SandboxEnvironment:
    registry_id: str
    files: dict[str, Path]
    backup_root: Path
    provider: FakeSystemProvider
    working_directory: str
    exec_start: list[str]
    version: str
    public_address: str
    _prepared: dict[str, Path] = field(default_factory=dict, compare=False, repr=False)

    def snapshot(self) -> dict[str, bytes]:
        result = {}
        for key, path in self.files.items():
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise DomainError("unsafe_managed_file")
            result[key] = path.read_bytes()
        return result

    def adoption_metadata(self, snapshot: dict[str, bytes]) -> tuple[dict, str, str]:
        return (
            preview(self.working_directory, self.exec_start, snapshot, self.version),
            self.public_address,
            self.registry_id,
        )

    @contextmanager
    def operation(self, operation_id: str):
        del operation_id
        yield

    def create_backup(self, operation_id: str, snapshot: dict[str, bytes]) -> None:
        private_backup(self.backup_root, operation_id, snapshot)

    def prepare_credentials(self, operation_id: str, content: bytes) -> None:
        self._prepared[operation_id] = prepare_atomic(
            self.files["credentials"], content, operation_id
        )

    def commit_credentials(self, operation_id: str, expected_hashes: dict[str, str]) -> None:
        if {key: source_hash(value) for key, value in self.snapshot().items()} != expected_hashes:
            raise SystemOperationError("drift_conflict")
        commit_atomic(self.files["credentials"], self._prepared.pop(operation_id))

    def restore_credentials(self, operation_id: str) -> None:
        backup = self.backup_root / operation_id / "credentials"
        atomic_replace(self.files["credentials"], backup.read_bytes())

    def cleanup(self, operation_id: str) -> None:
        temp = self._prepared.pop(operation_id, None)
        if temp:
            temp.unlink(missing_ok=True)
        candidate = self.files["credentials"].parent / (
            f".tunnelui-{operation_id}-{self.files['credentials'].name}"
        )
        candidate.unlink(missing_ok=True)


class TrustTunnelCoordinator:
    """Durable apply coordinator; privileged effects stay behind an environment."""

    def __init__(self, sessions: sessionmaker, box: SecretBox,
                 environments: dict[str, ManagedEnvironment], locks: InboundLocks | None = None):
        self.sessions = sessions
        self.box = box
        self.environments = environments
        self.locks = locks or InboundLocks()
        self.interrupt_at: str | None = None

    def _interrupt(self, checkpoint: str) -> None:
        if self.interrupt_at == checkpoint:
            self.interrupt_at = None
            raise SimulatedCrash(checkpoint)

    def environment(self, inbound: Inbound) -> ManagedEnvironment:
        environment = self.environments.get(inbound.registry_id or "")
        if environment is None:
            raise DomainError("sandbox_environment_unavailable", 503)
        return environment

    def _render_state(self, db: Session, inbound_id: str) -> tuple[bytes, dict[str, tuple[int, int]]]:
        rows = db.execute(select(Attachment, Client).join(
            Client, Client.id == Attachment.client_id
        ).where(Attachment.inbound_id == inbound_id).order_by(Client.username)).all()
        revisions = {
            attachment.id: (attachment.revision, client.revision)
            for attachment, client in rows
        }
        credentials = []
        for attachment, client in rows:
            if attachment.desired_state != "active" or not attachment.enabled:
                continue
            if not attachment.secret_ciphertext:
                raise DomainError("attachment_secret_missing")
            credentials.append(Credential(
                username=client.username,
                password=self.box.decrypt(attachment.secret_ciphertext),
                enabled=client.enabled,
                expires_at=client.expires_at if client.expiry_managed else None,
                max_http2_conns=attachment.max_http2_conns,
                max_http3_conns=attachment.max_http3_conns,
            ))
        return render_credentials(credentials, int(time.time())), revisions

    def _render(self, db: Session, inbound_id: str) -> bytes:
        return self._render_state(db, inbound_id)[0]

    @staticmethod
    def _fingerprint(inbound_id: str, kind: str, key: str) -> str:
        return hashlib.sha256(f"{inbound_id}\0{kind}\0{key}".encode()).hexdigest()

    def apply(self, inbound_id: str, idempotency_key: str, admin: str,
              kind: str = "credentials_apply",
              expected_expiry: tuple[str, int, int] | None = None) -> dict:
        with self.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound or inbound.kind != "trusttunnel":
                raise DomainError("inbound_not_found", 404)
            if not inbound.enabled:
                raise DomainError("inbound_not_managed")
            if inbound.config_state == "recovery_required":
                raise DomainError("recovery_required")
            environment = self.environment(inbound)
            fingerprint = self._fingerprint(inbound_id, kind, idempotency_key)
            existing = db.scalar(select(Operation).where(
                Operation.idempotency_key == idempotency_key
            ))
            if existing:
                if existing.request_fingerprint != fingerprint:
                    raise DomainError("idempotency_conflict")
                return operation_output(existing)
            with self.locks.acquire(inbound_id):
                operation_id = str(uuid.uuid4())
                with environment.operation(operation_id):
                    # The agent lock also serializes other backend processes. Refresh
                    # the DB after acquiring it so a concurrent worker cannot replay
                    # a stale render or create the same idempotent operation twice.
                    db.rollback()
                    inbound = db.get(Inbound, inbound_id)
                    existing = db.scalar(select(Operation).where(
                        Operation.idempotency_key == idempotency_key
                    ))
                    if existing:
                        if existing.request_fingerprint != fingerprint:
                            raise DomainError("idempotency_conflict")
                        return operation_output(existing)
                    if inbound.config_state == "recovery_required":
                        raise DomainError("recovery_required")
                    if active_operation(db, inbound_id):
                        raise DomainError("operation_in_progress")
                    if expected_expiry is not None:
                        client_id, deadline, revision = expected_expiry
                        client = db.get(Client, client_id)
                        attachment = db.scalar(select(Attachment).where(
                            Attachment.client_id == client_id,
                            Attachment.inbound_id == inbound_id,
                        ))
                        if (not client or not attachment or not client.enabled
                                or client.expires_at != deadline
                                or client.revision != revision
                                or deadline > int(time.time())):
                            raise DomainError("expiry_stale")
                        if attachment.applied_expired:
                            raise DomainError("expiry_already_applied")
                    content, rendered_revisions = self._render_state(db, inbound_id)
                    parse_credentials(content)
                    try:
                        original = environment.snapshot()
                    except OSError:
                        raise DomainError("management_unavailable", 503) from None
                    current_hashes = {key: source_hash(value) for key, value in original.items()}
                    expected = hashes(inbound.hashes_json)
                    operation = Operation(
                        id=operation_id,
                        inbound_id=inbound_id, kind=kind, state="pending",
                        idempotency_key=idempotency_key, request_fingerprint=fingerprint,
                        expected_hashes_json=json.dumps(current_hashes), result_hashes_json="{}",
                    )
                    db.add(operation)
                    db.commit()
                    self._interrupt("before_backup")
                    transition(db, operation, "preparing")
                    self._interrupt("after_preparing")
                    if current_hashes != expected:
                        inbound.config_state = "drift"
                        inbound.updated_at = now()
                        transition(db, operation, "failed", "drift_conflict")
                        record_result(db, admin, "config.apply", "inbound", inbound_id,
                                      "error", "Не удалось применить конфигурацию")
                        db.commit()
                        raise DomainError("drift_conflict")
                    environment.create_backup(operation.id, original)
                    operation.backup_id = operation.id
                    db.commit()
                    transition(db, operation, "backed_up")
                    self._interrupt("after_backup")
                    changed = False
                    try:
                        environment.prepare_credentials(operation.id, content)
                        operation.result_hashes_json = json.dumps({
                            **current_hashes, "credentials": source_hash(content),
                        })
                        transition(db, operation, "writing")
                        self._interrupt("after_temp_write")
                        if {
                            key: source_hash(value)
                            for key, value in environment.snapshot().items()
                        } != current_hashes:
                            raise SystemOperationError("drift_conflict")
                        changed = True
                        # A lost agent reply after rename leaves the replace outcome
                        # unknown. Treat the boundary as changed and use the backup.
                        environment.commit_credentials(operation.id, current_hashes)
                        transition(db, operation, "applying")
                        self._interrupt("after_replace")
                        environment.provider.restart(inbound_id)
                        transition(db, operation, "checking")
                        self._interrupt("after_restart")
                        self._interrupt("during_health")
                        if not environment.provider.health(inbound_id):
                            raise SystemOperationError("health_failed")
                        result = environment.snapshot()
                        if {
                            key: source_hash(value) for key, value in result.items()
                        } != hashes(operation.result_hashes_json):
                            raise SystemOperationError("post_apply_drift_conflict")
                    except SimulatedCrash:
                        raise
                    except (OSError, SystemOperationError) as error:
                        if str(error) == "drift_conflict":
                            # The agent reports this only before its rename; the
                            # operation must not restore over the external edit.
                            changed = False
                        if not changed:
                            try:
                                environment.cleanup(operation.id)
                            except OSError:
                                inbound.config_state = "recovery_required"
                                inbound.updated_at = now()
                                db.commit()
                                transition(db, operation, "needs_recovery", "cleanup_failed")
                                record_result(
                                    db, admin, "config.apply", "inbound", inbound_id,
                                    "error", "Операция требует восстановления"
                                )
                                db.commit()
                                return operation_output(operation)
                            transition(db, operation, "failed", str(error))
                            self._mark_error(db, inbound_id)
                            if str(error) == "drift_conflict":
                                inbound.config_state = "drift"
                                for attachment in db.scalars(select(Attachment).where(
                                    Attachment.inbound_id == inbound_id,
                                    Attachment.sync_state == "error",
                                )).all():
                                    attachment.sync_state = "conflict"
                            record_result(db, admin, "config.apply", "inbound", inbound_id,
                                          "error", "Не удалось применить конфигурацию")
                            db.commit()
                            return operation_output(operation)
                        return self._rollback(
                            db, inbound, operation, environment, admin, str(error)
                        )
                    result_hashes = {key: source_hash(value) for key, value in result.items()}
                    operation.result_hashes_json = json.dumps(result_hashes)
                    inbound.hashes_json = operation.result_hashes_json
                    inbound.config_state = "synced"
                    inbound.updated_at = now()
                    self._mark_applied(db, inbound_id, rendered_revisions)
                    db.commit()
                    transition(db, operation, "succeeded")
                    record_result(db, admin, "config.apply", "inbound", inbound_id,
                                  "success", "Конфигурация применена, сервис отвечает")
                    db.commit()
                    return operation_output(operation)

    def check_drift(self, inbound_id: str) -> dict:
        with self.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound:
                raise DomainError("inbound_not_found", 404)
            if active_operation(db, inbound_id):
                raise DomainError("operation_in_progress")
            environment = self.environment(inbound)
            with self.locks.acquire(inbound_id), environment.operation(str(uuid.uuid4())):
                try:
                    snapshot = environment.snapshot()
                except OSError:
                    raise DomainError("management_unavailable", 503) from None
                current = {key: source_hash(value) for key, value in snapshot.items()}
                drift = current != hashes(inbound.hashes_json)
                if inbound.config_state not in {"recovery_required", "unmanaged"}:
                    inbound.config_state = "drift" if drift else "synced"
                    inbound.updated_at = now()
                    db.commit()
            return {"drift": drift, "config_state": inbound.config_state,
                    "changed_files": sorted(
                        key for key, value in current.items()
                        if value != hashes(inbound.hashes_json).get(key)
                    )}

    def _pending_view(self, db: Session, inbound: Inbound,
                      environment: ManagedEnvironment) -> tuple[dict, list[Attachment]]:
        try:
            current = {
                key: source_hash(value) for key, value in environment.snapshot().items()
            }
        except OSError:
            raise DomainError("management_unavailable", 503) from None
        expected = hashes(inbound.hashes_json)
        rows = db.execute(select(Attachment, Client).join(
            Client, Client.id == Attachment.client_id
        ).where(Attachment.inbound_id == inbound.id).order_by(Client.username)).all()
        def is_pending(attachment: Attachment, client: Client) -> bool:
            return (
                attachment.desired_state != attachment.applied_state
                or (attachment.max_http2_conns, attachment.max_http3_conns) != (
                    attachment.applied_max_http2_conns,
                    attachment.applied_max_http3_conns,
                )
                or (attachment.applied_client_enabled is not None
                    and client.enabled != attachment.applied_client_enabled)
                or (attachment.applied_username is not None
                    and client.username != attachment.applied_username)
                or (attachment.applied_secret_ciphertext is not None
                    and attachment.secret_ciphertext != attachment.applied_secret_ciphertext)
                or (attachment.applied_expiry_managed is not None
                    and (client.expires_at != attachment.applied_expires_at
                         or client.expiry_managed != attachment.applied_expiry_managed))
            )
        changes = [{
            "attachment_id": attachment.id,
            "client_id": client.id,
            "username": client.username,
            "desired_state": attachment.desired_state,
            "applied_state": attachment.applied_state,
            "revision": attachment.revision,
            "desired_limits": [attachment.max_http2_conns, attachment.max_http3_conns],
            "applied_limits": [
                attachment.applied_max_http2_conns, attachment.applied_max_http3_conns
            ],
            "desired_global_enabled": client.enabled,
            "applied_global_enabled": attachment.applied_client_enabled,
            "applied_username": attachment.applied_username,
            "desired_expires_at": client.expires_at,
            "applied_expires_at": attachment.applied_expires_at,
            "credential_change": (attachment.applied_secret_ciphertext is not None
                                  and attachment.secret_ciphertext
                                  != attachment.applied_secret_ciphertext),
        } for attachment, client in rows
            if is_pending(attachment, client)]
        global_client_ids = sorted({
            client.id for attachment, client in rows
            if attachment.applied_client_enabled is not None
            and client.enabled != attachment.applied_client_enabled
        })
        affected = []
        for client_id in global_client_ids:
            for other, other_inbound in db.execute(select(Attachment, Inbound).join(
                Inbound, Inbound.id == Attachment.inbound_id
            ).where(Attachment.client_id == client_id).order_by(Inbound.name)).all():
                affected.append({
                    "client_id": client_id,
                    "inbound_id": other_inbound.id,
                    "inbound_name": other_inbound.name,
                    "applied_global_enabled": other.applied_client_enabled,
                    "attachment_revision": other.revision,
                    "inbound_state": other_inbound.config_state,
                    "inbound_hashes": hashes(other_inbound.hashes_json),
                })
        payload = {
            "inbound_id": inbound.id, "expected": expected, "current": current,
            "changes": changes, "global_impact": affected,
        }
        fingerprint = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        return {
            "fingerprint": fingerprint,
            "changed_files": sorted(
                key for key, value in current.items() if value != expected.get(key)
            ),
            "changes": changes,
            "affected_inbounds": [{key: item[key] for key in (
                "client_id", "inbound_id", "inbound_name", "applied_global_enabled"
            )} for item in affected],
        }, [attachment for attachment, client in rows if is_pending(attachment, client)]

    def pending_preview(self, inbound_id: str) -> dict:
        with self.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound:
                raise DomainError("inbound_not_found", 404)
            if active_operation(db, inbound_id):
                raise DomainError("operation_in_progress")
            environment = self.environment(inbound)
            with self.locks.acquire(inbound_id), environment.operation(str(uuid.uuid4())):
                view, _ = self._pending_view(db, inbound, environment)
                return view

    def apply_pending(self, inbound_id: str, key: str, admin: str) -> dict:
        with self.sessions() as db:
            existing = db.scalar(select(Operation).where(Operation.idempotency_key == key))
            if existing:
                if existing.request_fingerprint != self._fingerprint(
                    inbound_id, "pending_apply", key
                ):
                    raise DomainError("idempotency_conflict")
                return operation_output(existing)
        view = self.pending_preview(inbound_id)
        if not view["changes"]:
            raise DomainError("no_pending_changes")
        if view["changed_files"]:
            raise DomainError("drift_conflict")
        return self.apply(inbound_id, key, admin, "pending_apply")

    def cancel_pending(self, inbound_id: str, fingerprint: str, admin: str) -> dict:
        with self.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound:
                raise DomainError("inbound_not_found", 404)
            if inbound.config_state == "recovery_required":
                raise DomainError("recovery_required")
            if active_operation(db, inbound_id):
                raise DomainError("operation_in_progress")
            environment = self.environment(inbound)
            candidate_rows = db.execute(select(Attachment, Client).join(
                Client, Client.id == Attachment.client_id
            ).where(Attachment.inbound_id == inbound_id)).all()
            global_client_ids = {
                client.id for attachment, client in candidate_rows
                if attachment.applied_client_enabled is not None
                and client.enabled != attachment.applied_client_enabled
            }
            global_client_ids.update(
                client.id for attachment, client in candidate_rows
                if (attachment.applied_username is not None
                    and client.username != attachment.applied_username)
                or (attachment.applied_expiry_managed is not None
                    and (client.expires_at != attachment.applied_expires_at
                         or client.expiry_managed != attachment.applied_expiry_managed))
            )
            affected_ids = {inbound_id}
            for client_id in global_client_ids:
                affected_ids.update(db.scalars(select(Attachment.inbound_id).where(
                    Attachment.client_id == client_id
                )).all())
            with ExitStack() as stack:
                for affected_id in sorted(affected_ids):
                    affected_inbound = db.get(Inbound, affected_id)
                    if (not affected_inbound or not affected_inbound.enabled
                            or affected_inbound.config_state == "recovery_required"
                            or active_operation(db, affected_id)):
                        raise DomainError("pending_cancel_unsafe")
                    stack.enter_context(self.locks.acquire(affected_id))
                    stack.enter_context(self.environment(affected_inbound).operation(
                        str(uuid.uuid4())
                    ))
                view, rows = self._pending_view(db, inbound, environment)
                if view["fingerprint"] != fingerprint:
                    raise DomainError("pending_preview_changed")
                usernames = {
                    item["attachment_id"]: item["username"] for item in view["changes"]
                }
                if any(attachment.applied_state == "pending" for attachment in rows):
                    try:
                        actual = {
                            credential.username for credential in parse_credentials(
                                environment.snapshot()["credentials"]
                            )
                        }
                    except OSError:
                        raise DomainError("management_unavailable", 503) from None
                    if any(
                        usernames[attachment.id] in actual
                        for attachment in rows if attachment.applied_state == "pending"
                    ):
                        raise DomainError("pending_cancel_unsafe")
                global_clients = {
                    attachment.client_id for attachment in rows
                    if attachment.applied_client_enabled is not None
                    and db.get(Client, attachment.client_id).enabled
                    != attachment.applied_client_enabled
                }
                for client_id in global_clients:
                    all_rows = db.scalars(select(Attachment).where(
                        Attachment.client_id == client_id
                    )).all()
                    if any(row.applied_client_enabled is None for row in all_rows):
                        raise DomainError("pending_cancel_unsafe")
                    client = db.get(Client, client_id)
                    target = next(row.applied_client_enabled for row in rows
                                  if row.client_id == client_id)
                    client.enabled = target
                    client.revision += 1
                    client.updated_at = now()
                    for other in all_rows:
                        if other.inbound_id != inbound_id:
                            other.sync_state = (
                                "pending" if other.applied_client_enabled != target
                                else other.sync_state
                            )
                            other.updated_at = now()
                identity_clients = {
                    attachment.client_id for attachment in rows
                    if (attachment.applied_username is not None
                        and db.get(Client, attachment.client_id).username
                        != attachment.applied_username)
                    or (attachment.applied_expiry_managed is not None
                        and (db.get(Client, attachment.client_id).expires_at
                             != attachment.applied_expires_at
                             or db.get(Client, attachment.client_id).expiry_managed
                             != attachment.applied_expiry_managed))
                }
                for client_id in identity_clients:
                    all_rows = db.scalars(select(Attachment).where(
                        Attachment.client_id == client_id
                    )).all()
                    targets = {
                        (row.applied_username, row.applied_expires_at,
                         row.applied_expiry_managed) for row in all_rows
                    }
                    if len(targets) != 1 or any(value is None for value in (
                        next(iter(targets))[0], next(iter(targets))[2]
                    )):
                        raise DomainError("pending_cancel_unsafe")
                    username, expires_at, expiry_managed = next(iter(targets))
                    client = db.get(Client, client_id)
                    client.username = username
                    client.expires_at = expires_at
                    client.expiry_managed = expiry_managed
                    client.revision += 1
                    client.updated_at = now()
                for attachment in rows:
                    if attachment.applied_state == "pending":
                        # A newly created attachment was never applied.
                        db.delete(attachment)
                        continue
                    attachment.desired_state = attachment.applied_state
                    attachment.enabled = attachment.applied_state == "active"
                    attachment.sync_state = attachment.applied_state
                    attachment.max_http2_conns = attachment.applied_max_http2_conns
                    attachment.max_http3_conns = attachment.applied_max_http3_conns
                    if attachment.applied_secret_ciphertext is not None:
                        attachment.secret_ciphertext = attachment.applied_secret_ciphertext
                    attachment.revision += 1
                    attachment.updated_at = now()
                if rows:
                    record_result(db, admin, "inbound.cancel_pending", "inbound",
                                  inbound_id, "success")
                if inbound.config_state == "error" and not view["changed_files"]:
                    try:
                        if environment.provider.health(inbound_id):
                            inbound.config_state = "synced"
                            inbound.updated_at = now()
                    except SystemOperationError:
                        pass
                db.commit()
                return {"cancelled": len(rows), "changed_files": view["changed_files"]}

    def reimport(self, inbound_id: str, admin: str) -> dict:
        with self.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound:
                raise DomainError("inbound_not_found", 404)
            if not inbound.enabled:
                raise DomainError("inbound_not_managed")
            if inbound.config_state == "recovery_required":
                raise DomainError("recovery_required")
            if active_operation(db, inbound_id):
                raise DomainError("operation_in_progress")
            environment = self.environment(inbound)
            with self.locks.acquire(inbound_id), environment.operation(str(uuid.uuid4())):
                try:
                    snapshot = environment.snapshot()
                except OSError:
                    raise DomainError("management_unavailable", 503) from None
                external = {
                    item.username: item for item in parse_credentials(snapshot["credentials"])
                }
                rows = db.execute(select(Attachment, Client).join(
                    Client, Client.id == Attachment.client_id
                ).where(Attachment.inbound_id == inbound_id)).all()
                local = {client.username: (attachment, client) for attachment, client in rows}
                if set(external) - set(local) or any(
                    attachment.applied_state == "active"
                    for username, (attachment, _client) in local.items()
                    if username not in external
                ) or any(
                    not local[username][1].enabled for username in external
                ):
                    raise DomainError("reimport_mapping_required")
                if any(
                    attachment.desired_state != attachment.applied_state
                    or (attachment.max_http2_conns, attachment.max_http3_conns) != (
                        attachment.applied_max_http2_conns,
                        attachment.applied_max_http3_conns,
                    )
                    or (attachment.applied_client_enabled is not None
                        and _client.enabled != attachment.applied_client_enabled)
                    or (attachment.applied_username is not None
                        and _client.username != attachment.applied_username)
                    or (attachment.applied_secret_ciphertext is not None
                        and attachment.secret_ciphertext != attachment.applied_secret_ciphertext)
                    or (attachment.applied_expiry_managed is not None
                        and (_client.expires_at != attachment.applied_expires_at
                             or _client.expiry_managed != attachment.applied_expiry_managed))
                    for attachment, _client in rows
                ):
                    raise DomainError("pending_changes_require_cancel")
                metadata, public_address, _service = environment.adoption_metadata(snapshot)
                if not metadata["verified"]:
                    raise DomainError("unsupported_endpoint_version")
                try:
                    unchanged = environment.snapshot() == snapshot
                except OSError:
                    raise DomainError("management_unavailable", 503) from None
                if not unchanged:
                    raise DomainError("drift_conflict")
                safe_metadata = {
                    key: value for key, value in metadata.items() if key != "hashes"
                }
                if (
                    hashes(inbound.hashes_json) == metadata["hashes"]
                    and json.loads(inbound.metadata_json or "{}") == safe_metadata
                    and inbound.public_address == public_address
                    and inbound.config_state == "synced"
                ):
                    return {"config_state": "synced", "client_count": len(external)}
                for username, credential in external.items():
                    attachment, _ = local[username]
                    attachment.secret_ciphertext = self.box.encrypt(credential.password)
                    attachment.applied_secret_ciphertext = attachment.secret_ciphertext
                    attachment.applied_username = username
                    attachment.max_http2_conns = credential.max_http2_conns
                    attachment.max_http3_conns = credential.max_http3_conns
                    attachment.applied_max_http2_conns = credential.max_http2_conns
                    attachment.applied_max_http3_conns = credential.max_http3_conns
                    attachment.applied_client_enabled = True
                    attachment.enabled = True
                    attachment.desired_state = "active"
                    attachment.applied_state = "active"
                    attachment.sync_state = "active"
                    attachment.updated_at = now()
                inbound.metadata_json = json.dumps(safe_metadata)
                inbound.public_address = public_address
                inbound.hashes_json = json.dumps(metadata["hashes"])
                inbound.config_state = "synced"
                inbound.updated_at = now()
                record_result(db, admin, "inbound.reimport", "inbound", inbound_id, "success")
                db.commit()
                return {"config_state": "synced", "client_count": len(external)}

    def restart(self, inbound_id: str, idempotency_key: str, admin: str) -> dict:
        with self.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound:
                raise DomainError("inbound_not_found", 404)
            if not inbound.enabled:
                raise DomainError("inbound_not_managed")
            if inbound.config_state == "recovery_required":
                raise DomainError("recovery_required")
            environment = self.environment(inbound)
            fingerprint = self._fingerprint(inbound_id, "restart", idempotency_key)
            existing = db.scalar(select(Operation).where(
                Operation.idempotency_key == idempotency_key
            ))
            if existing:
                if existing.request_fingerprint != fingerprint:
                    raise DomainError("idempotency_conflict")
                return operation_output(existing)
            with self.locks.acquire(inbound_id):
                operation_id = str(uuid.uuid4())
                with environment.operation(operation_id):
                    if active_operation(db, inbound_id):
                        raise DomainError("operation_in_progress")
                    try:
                        expected = {
                            key: source_hash(value)
                            for key, value in environment.snapshot().items()
                        }
                    except OSError:
                        raise DomainError("management_unavailable", 503) from None
                    if expected != hashes(inbound.hashes_json):
                        inbound.config_state = "drift"
                        inbound.updated_at = now()
                        db.commit()
                        raise DomainError("drift_conflict")
                    operation = Operation(
                        id=operation_id,
                        inbound_id=inbound_id, kind="restart", state="pending",
                        idempotency_key=idempotency_key, request_fingerprint=fingerprint,
                        expected_hashes_json=json.dumps(expected), result_hashes_json="{}",
                    )
                    db.add(operation)
                    db.commit()
                    transition(db, operation, "preparing")
                    transition(db, operation, "applying")
                    try:
                        environment.provider.restart(inbound_id)
                        transition(db, operation, "checking")
                        if not environment.provider.health(inbound_id):
                            raise SystemOperationError("health_failed")
                    except SystemOperationError as error:
                        inbound.config_state = "error"
                        db.commit()
                        transition(db, operation, "failed", str(error))
                        record_result(
                            db, admin, "service.restart", "inbound", inbound_id, "error"
                        )
                    else:
                        transition(db, operation, "succeeded")
                        record_result(
                            db, admin, "service.restart", "inbound", inbound_id, "success"
                        )
                    db.commit()
                    return operation_output(operation)

    def _mark_applied(self, db: Session, inbound_id: str,
                      rendered_revisions: dict[str, tuple[int, int]]) -> None:
        for attachment, client in db.execute(select(Attachment, Client).join(
            Client, Client.id == Attachment.client_id
        ).where(Attachment.inbound_id == inbound_id).execution_options(
            populate_existing=True
        )).all():
            if rendered_revisions.get(attachment.id) != (
                attachment.revision, client.revision
            ):
                attachment.sync_state = "pending"
                continue
            attachment.applied_state = (
                "active" if attachment.desired_state == "active" and attachment.enabled else "disabled"
            )
            attachment.sync_state = attachment.applied_state
            attachment.applied_max_http2_conns = attachment.max_http2_conns
            attachment.applied_max_http3_conns = attachment.max_http3_conns
            attachment.applied_client_enabled = client.enabled
            attachment.applied_username = client.username
            attachment.applied_secret_ciphertext = attachment.secret_ciphertext
            attachment.applied_expires_at = client.expires_at
            attachment.applied_expiry_managed = client.expiry_managed
            attachment.applied_expired = bool(
                client.expiry_managed and client.expires_at is not None
                and client.expires_at <= now() and client.enabled
                and attachment.desired_state == "active" and attachment.enabled
            )
            attachment.updated_at = now()

    def _mark_error(self, db: Session, inbound_id: str) -> None:
        inbound = db.get(Inbound, inbound_id)
        if inbound and inbound.config_state != "recovery_required":
            inbound.config_state = "error"
            inbound.updated_at = now()
        for attachment in db.scalars(select(Attachment).where(
            Attachment.inbound_id == inbound_id,
            (Attachment.desired_state != Attachment.applied_state)
            | (Attachment.sync_state == "pending"),
        )).all():
            attachment.sync_state = "error"
            attachment.updated_at = now()

    def _rollback(self, db: Session, inbound: Inbound, operation: Operation,
                  environment: ManagedEnvironment, admin: str, cause: str) -> dict:
        if operation.state != "rolling_back":
            transition(db, operation, "rolling_back", cause)
        self._interrupt("during_rollback")
        try:
            current = {
                key: source_hash(value)
                for key, value in environment.snapshot().items()
            }
            expected = hashes(operation.expected_hashes_json)
            written = hashes(operation.result_hashes_json)
            if any(
                current.get(key) != value
                for key, value in expected.items() if key != "credentials"
            ) or current.get("credentials") not in {
                expected.get("credentials"), written.get("credentials")
            }:
                raise SystemOperationError("rollback_drift_conflict")
            environment.cleanup(operation.id)
            environment.restore_credentials(operation.id)
            environment.provider.restart(inbound.id)
            if not environment.provider.health(inbound.id):
                raise SystemOperationError("rollback_health_failed")
            if {
                key: source_hash(value)
                for key, value in environment.snapshot().items()
            } != expected:
                raise SystemOperationError("rollback_drift_conflict")
        except (OSError, SystemOperationError) as error:
            inbound.config_state = "recovery_required"
            inbound.updated_at = now()
            self._mark_error(db, inbound.id)
            db.commit()
            transition(db, operation, "needs_recovery", (
                "rollback_drift_conflict" if str(error) == "rollback_drift_conflict"
                else "rollback_failed"
            ))
            record_result(db, admin, "config.rollback", "inbound", inbound.id,
                          "error", "Откат не завершён; требуется восстановление")
        else:
            inbound.hashes_json = operation.expected_hashes_json
            inbound.config_state = "synced"
            inbound.updated_at = now()
            self._mark_error(db, inbound.id)
            db.commit()
            transition(db, operation, "rolled_back", cause)
            record_result(db, admin, "config.rollback", "inbound", inbound.id,
                          "success", "Предыдущая конфигурация восстановлена")
        db.commit()
        return operation_output(operation)

    def recover_all(self) -> list[dict]:
        with self.sessions() as db:
            ids = db.scalars(select(Operation.id).where(
                Operation.state.in_(INCOMPLETE_STATES)
            ).order_by(Operation.created_at)).all()
        return [self.recover(operation_id) for operation_id in ids]

    def recover(self, operation_id: str, admin: str = "system") -> dict:
        with self.sessions() as db:
            operation = db.get(Operation, operation_id)
            if not operation:
                raise DomainError("operation_not_found", 404)
            if operation.state in {"succeeded", "rolled_back", "failed"}:
                return operation_output(operation)
            inbound = db.get(Inbound, operation.inbound_id)
            if not inbound:
                raise DomainError("inbound_not_found", 404)
            environment = self.environment(inbound)
            with self.locks.acquire(inbound.id), environment.operation(operation.id):
                current = {
                    key: source_hash(value) for key, value in environment.snapshot().items()
                }
                expected = hashes(operation.expected_hashes_json)
                environment.cleanup(operation.id)
                if operation.state in {"pending", "preparing"} or (
                    operation.state in {"backed_up", "writing"} and current == expected
                ):
                    transition(db, operation, "failed", "interrupted_before_replace")
                    return operation_output(operation)
                if operation.state in {"backed_up", "writing"}:
                    operation.state = "needs_recovery"
                    operation.error_code = "unexpected_filesystem_state"
                    operation.updated_at = now()
                    operation.completed_at = operation.updated_at
                    inbound.config_state = "recovery_required"
                    db.commit()
                    return operation_output(operation)
                if operation.state == "needs_recovery":
                    operation.state = "rolling_back"
                    operation.completed_at = None
                    db.commit()
                return self._rollback(
                    db, inbound, operation, environment, admin, "interrupted_operation"
                )
