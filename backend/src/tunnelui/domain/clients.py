from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    comment: str = Field(default="", max_length=4000)
    expires_at: int | None = Field(default=None, ge=0, le=253402300799)

    @field_validator("display_name")
    @classmethod
    def meaningful_name(cls, value):
        if not value.strip():
            raise ValueError("Display name must not be blank")
        return value.strip()


class ClientUpdate(ClientInput):
    revision: int = Field(ge=1)
