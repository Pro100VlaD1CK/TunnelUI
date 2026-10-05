from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

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


class AttachmentDetach(IdempotentAction):
    revision: int = Field(ge=1)


class ClientAccess(IdempotentAction):
    enabled: bool
    revision: int = Field(ge=1)


class ExportRequest(StrictModel):
    inbound_id: str = Field(min_length=36, max_length=36)
    format: Literal["deeplink", "toml"]


class FakeScenario(StrictModel):
    scenario: Literal[
        "healthy", "stopped", "restart_failure",
        "health_failure_rollback_success", "rollback_failure", "drift",
    ]
