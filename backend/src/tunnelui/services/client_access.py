"""Idempotent global access change with explicit per-inbound apply results."""

import hashlib
import json

from sqlalchemy import select

from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, Client, ClientAccessIntent, Inbound, Operation, now
from tunnelui.services.audit import record
from tunnelui.services.coordinator import TrustTunnelCoordinator
from tunnelui.services.operations import active_operation, operation_output
from tunnelui.system.provider import SystemOperationError


class ClientAccessService:
    def __init__(self, coordinator: TrustTunnelCoordinator, admin: str):
        self.coordinator = coordinator
        self.admin = admin

    @staticmethod
    def _operation_key(key: str, inbound_id: str) -> str:
        return "access-" + hashlib.sha256(f"{key}\0{inbound_id}".encode()).hexdigest()

    def change(self, client_id: str, enabled: bool, revision: int, key: str) -> dict:
        with self.coordinator.locks.acquire(f"client:{client_id}"):
            with self.coordinator.sessions() as db:
                intent = db.get(ClientAccessIntent, key)
                if intent:
                    if (intent.client_id, intent.enabled, intent.revision) != (
                        client_id, enabled, revision
                    ):
                        raise DomainError("idempotency_conflict")
                else:
                    client = db.get(Client, client_id)
                    if not client:
                        raise DomainError("client_not_found", 404)
                    if client.revision != revision:
                        raise DomainError("revision_conflict")
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
                    client.enabled = enabled
                    client.revision += 1
                    client.updated_at = now()
                    for row in rows:
                        row.sync_state = "pending"
                    for inbound_id in inbound_ids:
                        self.coordinator._render(db, inbound_id)
                    intent = ClientAccessIntent(
                        idempotency_key=key, client_id=client_id,
                        enabled=enabled, revision=revision,
                        inbound_ids_json=json.dumps(inbound_ids),
                        attachment_revisions_json=json.dumps({
                            row.id: row.revision for row in rows
                        }),
                    )
                    db.add(intent)
                    record(db, self.admin,
                           "client.edit" if enabled else "client.disable", "client", client_id)
                    db.commit()

                inbound_ids = json.loads(intent.inbound_ids_json)
                expected_revisions = json.loads(intent.attachment_revisions_json)
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
                        rows = db.scalars(select(Attachment).where(
                            Attachment.client_id == client_id
                        )).all()
                        if (not client or client.revision != revision + 1
                                or client.enabled != enabled
                                or {row.id: row.revision for row in rows}
                                != expected_revisions):
                            return self._partial(client_id, enabled, operations,
                                                 "revision_conflict")
                        try:
                            result = self.coordinator.apply(
                                inbound_id, operation_key, self.admin, "client_access"
                            )
                        except DomainError as error:
                            return self._partial(client_id, enabled, operations, error.code)
                        except (OSError, SystemOperationError):
                            return self._partial(client_id, enabled, operations,
                                                 "management_unavailable")
                    operations.append(result)
                    if result["state"] != "succeeded":
                        return self._partial(client_id, enabled, operations,
                                             result["error_code"] or "operation_failed")
                return {"client_id": client_id, "enabled": enabled,
                        "state": "succeeded", "error_code": None,
                        "operations": operations}

    @staticmethod
    def _partial(client_id: str, enabled: bool, operations: list[dict], code: str) -> dict:
        return {"client_id": client_id, "enabled": enabled,
                "state": "partial", "error_code": code,
                "operations": operations}
