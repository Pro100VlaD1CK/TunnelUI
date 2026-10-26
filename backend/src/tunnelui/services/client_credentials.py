"""Safe global username/expiry changes through the existing apply journal."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, Client, CredentialIntent, Inbound, Operation, now
from tunnelui.services.audit import record
from tunnelui.services.coordinator import TrustTunnelCoordinator
from tunnelui.services.operations import active_operation, operation_output
from tunnelui.system.provider import SystemOperationError


class ClientCredentialService:
    def __init__(self, coordinator: TrustTunnelCoordinator, admin: str):
        self.coordinator, self.admin = coordinator, admin

    @staticmethod
    def _operation_key(key: str, inbound_id: str) -> str:
        return "credential-" + hashlib.sha256(f"{key}\0{inbound_id}".encode()).hexdigest()

    def change(self, client_id: str, username: str, expires_at: int | None,
               display_name: str, comment: str, revision: int, key: str) -> dict:
        fingerprint = self.coordinator.box.intent_fingerprint(json.dumps({
            "client_id": client_id, "username": username,
            "expires_at": expires_at, "display_name": display_name,
            "comment": comment, "revision": revision,
        }, sort_keys=True).encode())
        with self.coordinator.locks.acquire(f"client:{client_id}"):
            with self.coordinator.sessions() as db:
                intent = db.get(CredentialIntent, key)
                if intent:
                    if (intent.client_id != client_id or intent.kind != "identity"
                            or intent.request_fingerprint != fingerprint):
                        raise DomainError("idempotency_conflict")
                else:
                    client = db.get(Client, client_id)
                    if not client:
                        raise DomainError("client_not_found", 404)
                    if client.revision != revision:
                        raise DomainError("revision_conflict")
                    if db.scalar(select(Client.id).where(
                        Client.username == username, Client.id != client_id
                    )):
                        raise DomainError("duplicate_username")
                    rows = db.scalars(select(Attachment).where(
                        Attachment.client_id == client_id
                    )).all()
                    inbound_ids = sorted({row.inbound_id for row in rows})
                    for inbound_id in inbound_ids:
                        inbound = db.get(Inbound, inbound_id)
                        if not inbound or not inbound.enabled:
                            raise DomainError("inbound_not_managed")
                        if inbound.config_state == "recovery_required":
                            raise DomainError("recovery_required")
                        if active_operation(db, inbound_id):
                            raise DomainError("operation_in_progress")
                        preview = self.coordinator.pending_preview(inbound_id)
                        if preview["changed_files"]:
                            raise DomainError("drift_conflict")
                        if preview["changes"]:
                            raise DomainError("pending_changes_require_cancel")
                    client.username = username
                    client.display_name = display_name
                    client.comment = comment
                    if client.expires_at != expires_at:
                        client.expiry_managed = True
                    client.expires_at = expires_at
                    client.revision += 1
                    client.updated_at = now()
                    for row in rows:
                        row.sync_state = "pending"
                    for inbound_id in inbound_ids:
                        self.coordinator._render(db, inbound_id)
                    intent = CredentialIntent(
                        idempotency_key=key, client_id=client_id, kind="identity",
                        request_fingerprint=fingerprint,
                        inbound_ids_json=json.dumps(inbound_ids), revision=revision,
                    )
                    db.add(intent)
                    record(db, self.admin, "client.credentials", "client", client_id)
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        raise DomainError("duplicate_username") from None
                inbound_ids = json.loads(intent.inbound_ids_json)
                operations = []
                for inbound_id in inbound_ids:
                    operation_key = self._operation_key(key, inbound_id)
                    operation = db.scalar(select(Operation).where(
                        Operation.idempotency_key == operation_key
                    ))
                    if operation:
                        result = operation_output(operation)
                    else:
                        db.expire_all()
                        client = db.get(Client, client_id)
                        if (not client or client.revision != revision + 1
                                or client.username != username
                                or client.expires_at != expires_at
                                or client.display_name != display_name
                                or client.comment != comment):
                            return self._partial(client_id, operations, "revision_conflict")
                        try:
                            result = self.coordinator.apply(
                                inbound_id, operation_key, self.admin, "client_credentials"
                            )
                        except DomainError as error:
                            return self._partial(client_id, operations, error.code)
                        except (OSError, SystemOperationError):
                            return self._partial(client_id, operations,
                                                 "management_unavailable")
                    operations.append(result)
                    if result["state"] != "succeeded":
                        return self._partial(client_id, operations,
                                             result["error_code"] or "operation_failed")
                    db.expire_all()
                    current = db.get(Client, client_id)
                    applied = db.scalar(select(Attachment).where(
                        Attachment.client_id == client_id,
                        Attachment.inbound_id == inbound_id,
                    ))
                    if (not current or current.revision != revision + 1
                            or not applied or applied.sync_state != "active"
                            or applied.applied_username != username
                            or applied.applied_expires_at != expires_at
                            or applied.applied_expiry_managed != current.expiry_managed):
                        return self._partial(client_id, operations, "apply_not_confirmed")
                return {"client_id": client_id, "state": "succeeded",
                        "error_code": None, "operations": operations}

    @staticmethod
    def _partial(client_id: str, operations: list[dict], code: str) -> dict:
        return {"client_id": client_id, "state": "partial",
                "error_code": code, "operations": operations}
