"""Revoke every TrustTunnel attachment before removing a global client."""

import hashlib
import json
import uuid
from contextlib import ExitStack

from sqlalchemy import select

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.trusttunnel import parse_credentials, source_hash
from tunnelui.models import Attachment, Client, ClientDeletion, Inbound, now
from tunnelui.services.audit import record
from tunnelui.services.coordinator import TrustTunnelCoordinator
from tunnelui.services.operations import active_operation, hashes
from tunnelui.system.provider import SystemOperationError


class ClientDeletionService:
    def __init__(self, coordinator: TrustTunnelCoordinator, admin: str):
        self.coordinator = coordinator
        self.admin = admin
        self.locks = coordinator.locks

    @staticmethod
    def _operation_key(key: str, inbound_id: str) -> str:
        return "delete-" + hashlib.sha256(f"{key}\0{inbound_id}".encode()).hexdigest()

    def _partial(self, key: str, client_id: str, operations: list[dict], code: str) -> dict:
        result = {
            "client_id": client_id, "state": "partial", "error_code": code,
            "operations": operations,
        }
        with self.coordinator.sessions() as db:
            deletion = db.get(ClientDeletion, key)
            deletion.state = "partial"
            deletion.result_json = json.dumps(result)
            deletion.updated_at = now()
            db.commit()
        return result

    def delete(self, client_id: str, revision: int, key: str) -> dict:
        # The client lock serializes this workflow within a backend process. Each
        # apply also takes the agent's per-inbound OS lock in Linux mode.
        with self.locks.acquire(f"client:{client_id}"):
            with self.coordinator.sessions() as db:
                deletion = db.get(ClientDeletion, key)
                if deletion and (deletion.client_id != client_id or deletion.revision != revision):
                    raise DomainError("idempotency_conflict")
                if deletion and deletion.state in {"succeeded", "partial"}:
                    return json.loads(deletion.result_json)
                if db.scalar(select(ClientDeletion).where(
                    ClientDeletion.client_id == client_id,
                    ClientDeletion.idempotency_key != key,
                    ClientDeletion.state == "running",
                )):
                    raise DomainError("operation_in_progress")
                client = db.get(Client, client_id)
                if not client:
                    raise DomainError("client_not_found", 404)
                if client.revision != revision:
                    raise DomainError("revision_conflict")
                rows = db.scalars(select(Attachment).where(
                    Attachment.client_id == client_id
                ).order_by(Attachment.inbound_id)).all()
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
                    if any(
                        change["client_id"] != client_id
                        or change["desired_state"] != "detached"
                        for change in preview["changes"]
                    ):
                        raise DomainError("pending_changes_require_cancel")
                    if preview["changed_files"]:
                        raise DomainError("drift_conflict")
                if not deletion:
                    for row in rows:
                        row.desired_state = "detached"
                        row.enabled = False
                        row.sync_state = "pending"
                        row.revision += 1
                        row.updated_at = now()
                    # Check the v1.1.0 non-empty credentials constraint for
                    # *every* inbound before committing any desired changes.
                    for inbound_id in inbound_ids:
                        self.coordinator._render(db, inbound_id)
                    deletion = ClientDeletion(
                        idempotency_key=key, client_id=client_id, revision=revision,
                        state="running", result_json="{}", updated_at=now(),
                    )
                    db.add(deletion)
                    db.commit()
            operations: list[dict] = []
            for inbound_id in inbound_ids:
                try:
                    operation = self.coordinator.apply(
                        inbound_id, self._operation_key(key, inbound_id),
                        self.admin, "client_delete",
                    )
                except DomainError as error:
                    return self._partial(key, client_id, operations, error.code)
                except (OSError, SystemOperationError):
                    return self._partial(key, client_id, operations, "management_unavailable")
                operations.append(operation)
                if operation["state"] != "succeeded":
                    return self._partial(
                        key, client_id, operations,
                        operation["error_code"] or "operation_failed",
                    )
            try:
                with self.coordinator.sessions() as db, ExitStack() as stack:
                    client = db.get(Client, client_id)
                    if not client or client.revision != revision:
                        raise DomainError("revision_conflict")
                    for inbound_id in inbound_ids:
                        stack.enter_context(self.locks.acquire(inbound_id))
                        inbound = db.get(Inbound, inbound_id)
                        stack.enter_context(
                            self.coordinator.environment(inbound).operation(str(uuid.uuid4()))
                        )
                    for inbound_id in inbound_ids:
                        inbound = db.get(Inbound, inbound_id)
                        if active_operation(db, inbound_id):
                            raise DomainError("operation_in_progress")
                        snapshot = self.coordinator.environment(inbound).snapshot()
                        actual = {name: source_hash(value) for name, value in snapshot.items()}
                        if actual != hashes(inbound.hashes_json):
                            inbound.config_state = "drift"
                            db.commit()
                            raise DomainError("drift_conflict")
                        if any(
                            credential.username == client.username
                            for credential in parse_credentials(snapshot["credentials"])
                        ):
                            raise DomainError("client_still_applied")
                    for row in db.scalars(select(Attachment).where(
                        Attachment.client_id == client_id
                    )).all():
                        if row.applied_state != "disabled":
                            raise DomainError("client_still_applied")
                        db.delete(row)
                    db.flush()
                    db.delete(client)
                    record(db, self.admin, "client.delete", "client", client_id)
                    result = {
                        "client_id": client_id, "state": "succeeded",
                        "error_code": None, "operations": operations,
                    }
                    deletion = db.get(ClientDeletion, key)
                    deletion.state = "succeeded"
                    deletion.result_json = json.dumps(result)
                    deletion.updated_at = now()
                    db.commit()
                    return result
            except DomainError as error:
                return self._partial(key, client_id, operations, error.code)
            except (OSError, SystemOperationError):
                return self._partial(key, client_id, operations, "management_unavailable")
