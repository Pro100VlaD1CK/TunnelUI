import ipaddress

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from tunnelui.domain.errors import DomainError


class ClientMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    username: str
    ip: str | None
    sessions: int = Field(ge=0)
    inbound: int = Field(ge=0)
    outbound: int = Field(ge=0)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value):
        if value is not None:
            ipaddress.ip_address(value)
        return value

    @property
    def online(self) -> bool:
        return self.sessions > 0


def parse_clients(payload: bytes) -> list[ClientMetrics]:
    if len(payload) > 2 * 1024 * 1024:
        raise DomainError("metrics_payload_too_large")
    try:
        return TypeAdapter(list[ClientMetrics]).validate_json(payload)
    except ValidationError:
        raise DomainError("invalid_metrics_response") from None


def validate_loopback_listener(address: str):
    try:
        host, port = address.rsplit(":", 1)
        ip = ipaddress.ip_address(host.strip("[]"))
        if not ip.is_loopback or not 1 <= int(port) <= 65535:
            raise ValueError
    except ValueError:
        raise DomainError("metrics_must_bind_loopback") from None
