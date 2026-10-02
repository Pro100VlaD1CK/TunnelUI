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
