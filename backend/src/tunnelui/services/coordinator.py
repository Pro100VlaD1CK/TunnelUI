import hashlib
import json
import time
import uuid
from contextlib import contextmanager
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

    def _render(self, db: Session, inbound_id: str) -> bytes:
        rows = db.execute(select(Attachment, Client).join(
            Client, Client.id == Attachment.client_id
        ).where(Attachment.inbound_id == inbound_id).order_by(Client.username)).all()
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
                expires_at=client.expires_at,
                max_http2_conns=attachment.max_http2_conns,
                max_http3_conns=attachment.max_http3_conns,
            ))
        return render_credentials(credentials, int(time.time()))

    @staticmethod
    def _fingerprint(inbound_id: str, kind: str, key: str) -> str:
        return hashlib.sha256(f"{inbound_id}\0{kind}\0{key}".encode()).hexdigest()

    def apply(self, inbound_id: str, idempotency_key: str, admin: str,
              kind: str = "credentials_apply") -> dict:
        with self.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound or inbound.kind != "trusttunnel":
                raise DomainError("inbound_not_found", 404)
            if not inbound.enabled:
                raise DomainError("inbound_not_managed")
            if inbound.config_state == "recovery_required":
                raise DomainError("recovery_required")
            content = self._render(db, inbound_id)
            parse_credentials(content)
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
                    if active_operation(db, inbound_id):
                        raise DomainError("operation_in_progress")
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
                    self._mark_applied(db, inbound_id)
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

    def cancel_pending(self, inbound_id: str) -> None:
        with self.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound:
                raise DomainError("inbound_not_found", 404)
            for attachment in db.scalars(select(Attachment).where(
                Attachment.inbound_id == inbound_id
            )).all():
                attachment.desired_state = attachment.applied_state
                attachment.enabled = attachment.applied_state == "active"
                attachment.sync_state = attachment.applied_state
                attachment.updated_at = now()
            db.commit()

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
                if set(local) != set(external):
                    raise DomainError("reimport_mapping_required")
                if any(
                    attachment.desired_state != attachment.applied_state
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
                    attachment.max_http2_conns = credential.max_http2_conns
                    attachment.max_http3_conns = credential.max_http3_conns
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

    def _mark_applied(self, db: Session, inbound_id: str) -> None:
        for attachment in db.scalars(select(Attachment).where(
            Attachment.inbound_id == inbound_id
        )).all():
            attachment.applied_state = (
                "active" if attachment.desired_state == "active" and attachment.enabled else "disabled"
            )
            attachment.sync_state = attachment.applied_state
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
