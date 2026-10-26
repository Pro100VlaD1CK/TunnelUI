import time
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tunnelui.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> int:
    return int(time.time())


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[int | None] = mapped_column(Integer)
    # Existing expiry values are inert until the administrator opts in.
    expiry_managed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    updated_at: Mapped[int] = mapped_column(Integer, default=now)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Inbound(Base):
    __tablename__ = "inbounds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32))
    registry_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    public_address: Mapped[str | None] = mapped_column(String(255))
    config_state: Mapped[str] = mapped_column(String(32), default="synced")
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    updated_at: Mapped[int] = mapped_column(Integer, default=now)


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (UniqueConstraint("client_id", "inbound_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    inbound_id: Mapped[str] = mapped_column(ForeignKey("inbounds.id", ondelete="RESTRICT"))
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    applied_secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    applied_username: Mapped[str | None] = mapped_column(String(64))
    applied_expires_at: Mapped[int | None] = mapped_column(Integer)
    applied_expiry_managed: Mapped[bool | None] = mapped_column(Boolean)
    applied_expired: Mapped[bool | None] = mapped_column(Boolean)
    max_http2_conns: Mapped[int | None] = mapped_column(Integer)
    max_http3_conns: Mapped[int | None] = mapped_column(Integer)
    applied_max_http2_conns: Mapped[int | None] = mapped_column(Integer)
    applied_max_http3_conns: Mapped[int | None] = mapped_column(Integer)
    applied_client_enabled: Mapped[bool | None] = mapped_column(Boolean)
    sync_state: Mapped[str] = mapped_column(String(32), default="pending")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    desired_state: Mapped[str] = mapped_column(String(32), default="active")
    applied_state: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    updated_at: Mapped[int] = mapped_column(Integer, default=now)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inbound_id: Mapped[str] = mapped_column(
        ForeignKey("inbounds.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    expected_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    result_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    backup_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    updated_at: Mapped[int] = mapped_column(Integer, default=now)
    completed_at: Mapped[int | None] = mapped_column(Integer)


class ClientDeletion(Base):
    """Secret-free replay record retained after the client row is removed."""

    __tablename__ = "client_deletions"
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32))
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[int] = mapped_column(Integer, default=now)


class AttachmentIntent(Base):
    """Idempotency metadata; fingerprint is keyed and stores no secret text."""

    __tablename__ = "attachment_intents"
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    attachment_id: Mapped[str] = mapped_column(String(36))
    client_id: Mapped[str] = mapped_column(String(36))
    inbound_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class ClientAccessIntent(Base):
    """Replay metadata for a global access change across managed inbounds."""

    __tablename__ = "client_access_intents"
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean)
    inbound_ids_json: Mapped[str] = mapped_column(Text)
    attachment_revisions_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class CredentialIntent(Base):
    __tablename__ = "credential_intents"
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    inbound_ids_json: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class ExpirySetting(Base):
    __tablename__ = "expiry_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[int] = mapped_column(Integer, default=now)


class ExpiryAttempt(Base):
    __tablename__ = "expiry_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    inbound_id: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[int | None] = mapped_column(Integer)
    operation_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[int] = mapped_column(Integer, default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    timestamp: Mapped[int] = mapped_column(Integer, default=now, index=True)
    admin: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(String(256))
