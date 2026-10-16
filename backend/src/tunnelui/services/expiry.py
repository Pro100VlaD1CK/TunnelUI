"""Opt-in, restart-safe expiry scan; intended for a systemd oneshot timer."""

import hashlib
import json
import time

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, Client, ExpiryAttempt, ExpirySetting, Inbound, now
from tunnelui.services.audit import record, record_result
from tunnelui.services.coordinator import TrustTunnelCoordinator
from tunnelui.services.operations import active_operation, safe_error_code
from tunnelui.system.provider import SystemOperationError


class ExpiryScheduler:
    def __init__(self, coordinator: TrustTunnelCoordinator):
        self.coordinator = coordinator

    def preview(self) -> dict:
        with self.coordinator.sessions() as db:
            setting = db.get(ExpirySetting, 1)
            if setting is None:
                raise DomainError("migration_required")
            rows = db.execute(select(Client, Attachment, Inbound).join(
                Attachment, Attachment.client_id == Client.id
            ).join(Inbound, Inbound.id == Attachment.inbound_id).where(
                Client.expires_at.is_not(None), Client.enabled.is_(True),
                Attachment.applied_state == "active", Inbound.enabled.is_(True),
            ).order_by(Client.username, Inbound.name)).all()
            current = int(time.time())
            affected = [{
                "client_id": client.id, "username": client.username,
                "inbound_id": inbound.id, "inbound_name": inbound.name,
                "expires_at": client.expires_at,
                "revision": client.revision,
                "overdue": client.expires_at <= current,
                "revoked": bool(attachment.applied_expired),
            } for client, attachment, inbound in rows]
            fingerprint = hashlib.sha256(json.dumps(affected, sort_keys=True).encode()).hexdigest()
            return {"enabled": setting.enabled, "fingerprint": fingerprint,
                    "affected": affected}

    def set_enabled(self, enabled: bool, fingerprint: str, admin: str) -> dict:
        view = self.preview()
        if view["fingerprint"] != fingerprint:
            raise DomainError("expiry_preview_changed")
        with self.coordinator.sessions() as db:
            setting = db.get(ExpirySetting, 1)
            setting.enabled = enabled
            setting.updated_at = now()
            record(db, admin, "expiry.enable" if enabled else "expiry.disable",
                   "expiry", "global")
            db.commit()
        return {"enabled": enabled}

    @staticmethod
    def _id(client_id: str, inbound_id: str, expires_at: int) -> str:
        return hashlib.sha256(f"{client_id}\0{inbound_id}\0{expires_at}".encode()).hexdigest()[:36]

    def retry(self, attempt_id: str, admin: str) -> dict:
        with self.coordinator.sessions() as db:
            row = db.get(ExpiryAttempt, attempt_id)
            if not row:
                raise DomainError("expiry_attempt_not_found", 404)
            if row.state not in {"error", "blocked"}:
                raise DomainError("expiry_retry_unavailable")
            row.next_attempt_at = 0
            row.updated_at = now()
            record(db, admin, "expiry.retry", "expiry", row.id)
            db.commit()
            return {"id": row.id, "state": row.state}

    def run_once(self, at: int | None = None) -> dict:
        current = int(time.time()) if at is None else at
        with self.coordinator.sessions() as db:
            setting = db.get(ExpirySetting, 1)
            if not setting or not setting.enabled:
                return {"enabled": False, "processed": 0, "succeeded": 0, "errors": 0}
            candidates = set(db.execute(select(Client.id, Attachment.inbound_id,
                Client.expires_at)
                .join(Attachment, Attachment.client_id == Client.id)
                .where(Client.expires_at.is_not(None), Client.expires_at <= current,
                       Client.enabled.is_(True),
                       Attachment.applied_state == "active")).all())
        outcomes = []
        for client_id, inbound_id, expires_at in sorted(candidates):
            try:
                outcome = self._process(client_id, inbound_id, expires_at, current)
            except DomainError as error:
                if error.code != "operation_in_progress":
                    raise
                continue
            if outcome:
                outcomes.append(outcome)
        return {"enabled": True, "processed": len(outcomes),
                "succeeded": outcomes.count("succeeded"),
                "errors": len(outcomes) - outcomes.count("succeeded")}

    def _process(self, client_id: str, inbound_id: str, expires_at: int,
                 current: int) -> str | None:
        attempt_id = self._id(client_id, inbound_id, expires_at)
        with self.coordinator.locks.acquire(f"client:{client_id}"):
            with self.coordinator.sessions() as db:
                setting = db.get(ExpirySetting, 1)
                client = db.get(Client, client_id)
                attachment = db.scalar(select(Attachment).where(
                    Attachment.client_id == client_id, Attachment.inbound_id == inbound_id
                ))
                inbound = db.get(Inbound, inbound_id)
                if (not setting or not setting.enabled or not client or not attachment
                        or not inbound or not inbound.enabled or not client.enabled
                        or client.expires_at != expires_at or expires_at > current
                        or attachment.applied_state != "active"):
                    return None
                row = db.get(ExpiryAttempt, attempt_id)
                if attachment.applied_expired:
                    if row is None:
                        row = ExpiryAttempt(
                            id=attempt_id, client_id=client_id, inbound_id=inbound_id,
                            expires_at=expires_at, state="succeeded", attempts=0,
                            next_attempt_at=None, updated_at=current,
                        )
                        db.add(row)
                    elif row.state != "succeeded":
                        row.state = "succeeded"
                        row.error_code = None
                        row.next_attempt_at = None
                        row.updated_at = current
                    else:
                        return None
                    try:
                        record(db, "expiry-worker", "expiry.revoke", "client", client_id)
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        return None
                    return "succeeded"
                if row is None:
                    row = ExpiryAttempt(id=attempt_id, client_id=client_id,
                                        inbound_id=inbound_id, expires_at=expires_at,
                                        state="pending", attempts=0, next_attempt_at=0)
                    db.add(row)
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        return None
                if row.next_attempt_at and row.next_attempt_at > current:
                    return None
                if row.state == "succeeded":
                    return None
                if row.state == "running":
                    sequence = row.attempts  # Replay the same apply after a lost reply/crash.
                else:
                    sequence = row.attempts + 1
                claimed = db.execute(update(ExpiryAttempt).where(
                    ExpiryAttempt.id == attempt_id,
                    ExpiryAttempt.attempts == row.attempts,
                    ExpiryAttempt.state == row.state,
                    ExpiryAttempt.next_attempt_at == row.next_attempt_at,
                ).values(state="running", attempts=sequence,
                         next_attempt_at=current + 300, updated_at=current))
                db.commit()
                if claimed.rowcount != 1:
                    return None
                operation_key = "expiry-" + hashlib.sha256(
                    f"{attempt_id}\0{sequence}".encode()
                ).hexdigest()
                try:
                    if inbound.config_state == "recovery_required":
                        raise DomainError("recovery_required")
                    if active_operation(db, inbound_id):
                        raise DomainError("operation_in_progress")
                    preview = self.coordinator.pending_preview(inbound_id)
                    if preview["changed_files"]:
                        raise DomainError("drift_conflict")
                    if any(
                        change["client_id"] != client_id
                        or change["desired_state"] != change["applied_state"]
                        or change["desired_limits"] != change["applied_limits"]
                        or change["desired_global_enabled"]
                           != change["applied_global_enabled"]
                        or change["applied_username"] not in (None, client.username)
                        or change["credential_change"]
                        for change in preview["changes"]
                    ):
                        raise DomainError("pending_changes_require_cancel")
                    client.expiry_managed = True
                    attachment.sync_state = "pending"
                    db.commit()
                    # Render again immediately before the privileged apply.
                    self.coordinator._render(db, inbound_id)
                    result = self.coordinator.apply(
                        inbound_id, operation_key, "expiry-worker", "expiry_revoke",
                        expected_expiry=(client_id, expires_at, client.revision),
                    )
                    db.expire_all()
                    refreshed = db.get(Attachment, attachment.id)
                    final = db.get(Client, client_id)
                    if (result["state"] == "succeeded" and refreshed.applied_expired
                            and final.expires_at == expires_at):
                        row = db.get(ExpiryAttempt, attempt_id)
                        row.state = "succeeded"
                        row.operation_id = result["id"]
                        row.error_code = None
                        row.next_attempt_at = None
                        row.updated_at = current
                        record(db, "expiry-worker", "expiry.revoke", "client", client_id)
                        db.commit()
                        return "succeeded"
                    code = result["error_code"] or "expiry_not_confirmed"
                    recovery = result["state"] == "needs_recovery"
                    operation_id = result["id"]
                except DomainError as error:
                    if error.code in {"expiry_already_applied", "expiry_stale"}:
                        db.rollback()
                        row = db.get(ExpiryAttempt, attempt_id)
                        row.state = ("succeeded" if error.code == "expiry_already_applied"
                                     else "superseded")
                        row.error_code = None
                        row.next_attempt_at = None
                        row.updated_at = current
                        db.commit()
                        return "succeeded" if error.code == "expiry_already_applied" else None
                    code, recovery, operation_id = error.code, False, None
                except (OSError, SystemOperationError):
                    code, recovery, operation_id = "management_unavailable", False, None
                db.rollback()
                row = db.get(ExpiryAttempt, attempt_id)
                blocked = recovery or code in {
                    "empty_credentials_unsupported_v1_1_0", "recovery_required"
                }
                row.state = "blocked" if blocked else "error"
                row.error_code = safe_error_code(code)
                row.operation_id = operation_id
                row.next_attempt_at = None if blocked else current + min(
                    3600, 60 * (2 ** min(row.attempts, 6))
                )
                row.updated_at = current
                record_result(db, "expiry-worker", "expiry.error", "client", client_id,
                              "error")
                db.commit()
                return "error"
