import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, AttachmentIntent, Client, Inbound, Operation, now
from tunnelui.security import SecretBox
from tunnelui.services.audit import record
from tunnelui.services.coordinator import TrustTunnelCoordinator
from tunnelui.services.operations import operation_output


class AttachmentService:
    def __init__(self, db: Session, admin: str, box: SecretBox,
                 coordinator: TrustTunnelCoordinator):
        self.db, self.admin, self.box, self.coordinator = db, admin, box, coordinator

    def _fingerprint(self, kind: str, payload: dict) -> str:
        content = json.dumps(
            {"kind": kind, **payload}, sort_keys=True, separators=(",", ":")
        ).encode()
        return self.box.intent_fingerprint(content)

    def _replay(self, key: str, kind: str, fingerprint: str):
        intent = self.db.get(AttachmentIntent, key)
        if not intent:
            if self.db.scalar(select(Operation).where(Operation.idempotency_key == key)):
                raise DomainError("idempotency_conflict")
            return None
        if intent.kind != kind or intent.request_fingerprint != fingerprint:
            raise DomainError("idempotency_conflict")
        operation = self.db.scalar(select(Operation).where(Operation.idempotency_key == key))
        if operation and (operation.kind != kind or operation.inbound_id != intent.inbound_id):
            raise DomainError("idempotency_conflict")
        result = operation_output(operation) if operation else self.coordinator.apply(
            intent.inbound_id, key, self.admin, kind
        )
        return intent, result

    def _intent(self, key: str, row: Attachment, kind: str, fingerprint: str):
        self.db.add(AttachmentIntent(
            idempotency_key=key, attachment_id=row.id, client_id=row.client_id,
            inbound_id=row.inbound_id, kind=kind, request_fingerprint=fingerprint,
        ))

    def _ensure_clean(self, inbound_id: str) -> None:
        preview = self.coordinator.pending_preview(inbound_id)
        if preview["changed_files"]:
            raise DomainError("drift_conflict")
        if preview["changes"]:
            raise DomainError("pending_changes_require_cancel")

    def create(self, client_id: str, inbound_id: str, password: str,
               max_http2_conns: int | None, max_http3_conns: int | None,
               idempotency_key: str):
        kind = "attachment_create"
        fingerprint = self._fingerprint(kind, {
            "client_id": client_id, "inbound_id": inbound_id, "password": password,
            "max_http2_conns": max_http2_conns, "max_http3_conns": max_http3_conns,
        })
        replay = self._replay(idempotency_key, kind, fingerprint)
        if replay:
            return replay[0].attachment_id, replay[1]
        client = self.db.get(Client, client_id)
        inbound = self.db.get(Inbound, inbound_id)
        if not client:
            raise DomainError("client_not_found", 404)
        if not inbound or inbound.kind != "trusttunnel":
            raise DomainError("inbound_not_found", 404)
        self._ensure_clean(inbound_id)
        row = Attachment(
            client_id=client_id, inbound_id=inbound_id,
            secret_ciphertext=self.box.encrypt(password), enabled=True,
            desired_state="active", applied_state="pending", sync_state="pending",
            max_http2_conns=max_http2_conns, max_http3_conns=max_http3_conns,
        )
        try:
            self.db.add(row)
            self.db.flush()
            self._intent(idempotency_key, row, kind, fingerprint)
            record(self.db, self.admin, "attachment.create", "attachment", row.id)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise DomainError("attachment_already_exists") from None
        try:
            operation = self.coordinator.apply(inbound_id, idempotency_key, self.admin, kind)
        except DomainError as error:
            with self.coordinator.sessions() as db:
                current = db.get(Attachment, row.id)
                if current:
                    current.sync_state = "conflict" if error.code == "drift_conflict" else "error"
                    db.commit()
            raise
        return row.id, operation

    def set_enabled(self, attachment_id: str, enabled: bool, revision: int,
                    idempotency_key: str, limits: dict | None = None):
        kind = "attachment_update"
        fingerprint = self._fingerprint(kind, {
            "attachment_id": attachment_id, "enabled": enabled,
            "revision": revision, "limits": limits or {},
        })
        replay = self._replay(idempotency_key, kind, fingerprint)
        if replay:
            return replay[1]
        row = self.db.get(Attachment, attachment_id)
        if not row:
            raise DomainError("attachment_not_found", 404)
        if row.revision != revision:
            raise DomainError("revision_conflict")
        self._ensure_clean(row.inbound_id)
        original = (row.enabled, row.desired_state, row.sync_state,
                    row.max_http2_conns, row.max_http3_conns)
        row.enabled = enabled
        for name, value in (limits or {}).items():
            setattr(row, name, value)
        row.desired_state = "active" if enabled else "disabled"
        row.sync_state = "pending"
        row.revision += 1
        row.updated_at = now()
        try:
            # Preflight detects the v1.1.0 zero-active-client constraint before commit.
            self.coordinator._render(self.db, row.inbound_id)
        except DomainError:
            (row.enabled, row.desired_state, row.sync_state,
             row.max_http2_conns, row.max_http3_conns) = original
            self.db.rollback()
            raise
        record(self.db, self.admin, "attachment.update", "attachment", row.id)
        self._intent(idempotency_key, row, kind, fingerprint)
        self.db.commit()
        return self.coordinator.apply(row.inbound_id, idempotency_key, self.admin, kind)

    def detach(self, attachment_id: str, revision: int, idempotency_key: str):
        kind = "attachment_detach"
        fingerprint = self._fingerprint(kind, {
            "attachment_id": attachment_id, "revision": revision,
        })
        replay = self._replay(idempotency_key, kind, fingerprint)
        if replay:
            if replay[1]["state"] == "succeeded":
                self._finish_detach(replay[0].attachment_id)
            return replay[1]
        row = self.db.get(Attachment, attachment_id)
        if not row:
            raise DomainError("attachment_not_found", 404)
        if row.revision != revision:
            raise DomainError("revision_conflict")
        self._ensure_clean(row.inbound_id)
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
        self._intent(idempotency_key, row, kind, fingerprint)
        self.db.commit()
        operation = self.coordinator.apply(inbound_id, idempotency_key, self.admin, kind)
        if operation["state"] == "succeeded":
            self._finish_detach(row_id)
        return operation

    def _finish_detach(self, attachment_id: str):
        with self.coordinator.sessions() as db:
            attached = db.get(Attachment, attachment_id)
            if attached and attached.applied_state == "disabled":
                db.delete(attached)
                db.commit()

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
                "max_http2_conns": attachment.max_http2_conns,
                "max_http3_conns": attachment.max_http3_conns,
                "applied_max_http2_conns": attachment.applied_max_http2_conns,
                "applied_max_http3_conns": attachment.applied_max_http3_conns,
            })
        return result
