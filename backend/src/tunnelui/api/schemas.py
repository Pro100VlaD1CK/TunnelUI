from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from tunnelui.domain.clients import ClientInput, ClientUpdate  # noqa: F401


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)


class ClientOutput(ClientInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: int
    updated_at: int
    revision: int
    expiry_managed: bool


class ClientPage(BaseModel):
    items: list[ClientOutput]
    total: int


class AdoptionConfirm(StrictModel):
    preview_id: str = Field(min_length=16, max_length=128)


class IdempotentAction(StrictModel):
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")


class AttachmentCreate(IdempotentAction):
    inbound_id: str = Field(min_length=36, max_length=36)
    password: SecretStr = Field(min_length=12, max_length=1024)
    max_http2_conns: int | None = Field(default=None, ge=0, le=2**32 - 1)
    max_http3_conns: int | None = Field(default=None, ge=0, le=2**32 - 1)


class AttachmentState(IdempotentAction):
    enabled: bool
    revision: int = Field(ge=1)
    max_http2_conns: int | None = Field(default=None, ge=0, le=2**32 - 1)
    max_http3_conns: int | None = Field(default=None, ge=0, le=2**32 - 1)


class AttachmentDetach(IdempotentAction):
    revision: int = Field(ge=1)


class ClientAccess(IdempotentAction):
    enabled: bool
    revision: int = Field(ge=1)


class ClientCredentials(IdempotentAction):
    username: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=160)
    comment: str = Field(default="", max_length=4000)
    expires_at: int | None = Field(default=None, ge=0, le=253402300799)
    revision: int = Field(ge=1)

    @field_validator("display_name")
    @classmethod
    def meaningful_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Display name must not be blank")
        return value.strip()


class PasswordRotation(IdempotentAction):
    password: SecretStr = Field(min_length=12, max_length=1024)
    revision: int = Field(ge=1)


class ExpiryEnabled(StrictModel):
    enabled: bool
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ClientDelete(IdempotentAction):
    revision: int = Field(ge=1)


class PendingCancel(StrictModel):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExportRequest(StrictModel):
    inbound_id: str = Field(min_length=36, max_length=36)
    format: Literal["deeplink", "toml"]


class FakeScenario(StrictModel):
    scenario: Literal[
        "healthy", "stopped", "restart_failure",
        "health_failure_rollback_success", "rollback_failure", "drift",
    ]
