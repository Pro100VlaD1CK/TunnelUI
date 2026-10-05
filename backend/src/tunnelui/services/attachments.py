from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, Client, Inbound, now
from tunnelui.security import SecretBox
from tunnelui.services.audit import record
from tunnelui.services.coordinator import TrustTunnelCoordinator


class AttachmentService:
    def __init__(self, db: Session, admin: str, box: SecretBox,
                 coordinator: TrustTunnelCoordinator):
        self.db, self.admin, self.box, self.coordinator = db, admin, box, coordinator

    def create(self, client_id: str, inbound_id: str, password: str,
               max_http2_conns: int | None, max_http3_conns: int | None,
               idempotency_key: str):
        client = self.db.get(Client, client_id)
        inbound = self.db.get(Inbound, inbound_id)
        if not client:
            raise DomainError("client_not_found", 404)
        if not inbound or inbound.kind != "trusttunnel":
            raise DomainError("inbound_not_found", 404)
        row = Attachment(
            client_id=client_id, inbound_id=inbound_id,
            secret_ciphertext=self.box.encrypt(password), enabled=True,
            desired_state="active", applied_state="pending", sync_state="pending",
            max_http2_conns=max_http2_conns, max_http3_conns=max_http3_conns,
        )
        try:
            self.db.add(row)
            self.db.flush()
            record(self.db, self.admin, "attachment.create", "attachment", row.id)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise DomainError("attachment_already_exists") from None
        try:
            operation = self.coordinator.apply(inbound_id, idempotency_key, self.admin)
        except DomainError as error:
            with self.coordinator.sessions() as db:
                current = db.get(Attachment, row.id)
                if current:
                    current.sync_state = "conflict" if error.code == "drift_conflict" else "error"
                    db.commit()
            raise
        return row.id, operation

    def set_enabled(self, attachment_id: str, enabled: bool, revision: int,
                    idempotency_key: str):
        row = self.db.get(Attachment, attachment_id)
        if not row:
            raise DomainError("attachment_not_found", 404)
        if row.revision != revision:
            raise DomainError("revision_conflict")
        original = (row.enabled, row.desired_state, row.sync_state)
        row.enabled = enabled
        row.desired_state = "active" if enabled else "disabled"
        row.sync_state = "pending"
        row.revision += 1
        row.updated_at = now()
        try:
            # Preflight detects the v1.1.0 zero-active-client constraint before commit.
            self.coordinator._render(self.db, row.inbound_id)
        except DomainError:
            row.enabled, row.desired_state, row.sync_state = original
            self.db.rollback()
            raise
        record(self.db, self.admin, "attachment.update", "attachment", row.id)
        self.db.commit()
        return self.coordinator.apply(row.inbound_id, idempotency_key, self.admin)

    def detach(self, attachment_id: str, revision: int, idempotency_key: str):
        row = self.db.get(Attachment, attachment_id)
        if not row:
            raise DomainError("attachment_not_found", 404)
        if row.revision != revision:
            raise DomainError("revision_conflict")
        row.desired_state = "detached"
        row.enabled = False
        row.sync_state = "pending"
        row.revision += 1
        row.updated_at = now()
        try:
            self.coordinator._render(self.db, row.inbound_id)
        except DomainError:
            self.db.rollback()
            raise
        inbound_id, row_id = row.inbound_id, row.id
        record(self.db, self.admin, "attachment.detach", "attachment", row.id)
        self.db.commit()
        operation = self.coordinator.apply(inbound_id, idempotency_key, self.admin)
        if operation["state"] == "succeeded":
            with self.coordinator.sessions() as db:
                attached = db.get(Attachment, row_id)
                if attached:
                    db.delete(attached)
                    db.commit()
        return operation

    @staticmethod
    def summaries(db: Session, client_ids: list[str]) -> dict[str, list[dict]]:
        if not client_ids:
            return {}
        rows = db.execute(select(Attachment, Inbound).join(
            Inbound, Inbound.id == Attachment.inbound_id
        ).where(Attachment.client_id.in_(client_ids))).all()
        result: dict[str, list[dict]] = {}
        for attachment, inbound in rows:
            result.setdefault(attachment.client_id, []).append({
                "id": attachment.id, "inbound_id": inbound.id, "inbound_name": inbound.name,
                "enabled": attachment.enabled, "desired_state": attachment.desired_state,
                "applied_state": attachment.applied_state, "sync_state": attachment.sync_state,
                "revision": attachment.revision,
            })
        return result
