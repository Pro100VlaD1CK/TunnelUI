import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cidr: str | None = None
    client_random_prefix: str | None = None
    action: Literal["allow", "deny"]

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, value):
        if value is not None:
            ipaddress.ip_network(value, strict=True)
        return value

    @field_validator("client_random_prefix")
    @classmethod
    def validate_prefix(cls, value):
        if value is None:
            return value
        parts = value.split("/")
        if len(parts) > 2 or any(not re.fullmatch(r"(?:[a-fA-F0-9]{2}){1,32}", p) for p in parts):
            raise ValueError("Expected 1–32 bytes of hex with optional same-length mask")
        if len(parts) == 2 and len(parts[0]) != len(parts[1]):
            raise ValueError("Prefix and mask must have equal length")
        return value.lower()
