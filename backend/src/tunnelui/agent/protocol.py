import base64
import json
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 6 * 1024 * 1024
MAX_MESSAGE_BYTES = MAX_REQUEST_BYTES
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
MAX_MANAGED_CONTENT_BYTES = 4 * 1024 * 1024
MANAGED_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


class ProtocolError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArguments(StrictModel):
    pass


class OperationArguments(StrictModel):
    operation_id: uuid.UUID


class PrepareArguments(OperationArguments):
    content_b64: str = Field(
        min_length=1, max_length=((MAX_MANAGED_CONTENT_BYTES + 2) // 3) * 4
    )

    def content(self) -> bytes:
        try:
            value = base64.b64decode(self.content_b64, validate=True)
        except ValueError as error:
            raise ProtocolError("invalid_content_encoding") from error
        if len(value) > MAX_MANAGED_CONTENT_BYTES:
            raise ProtocolError("invalid_managed_content")
        return value


class ExportArguments(StrictModel):
    username: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
    format: Literal["deeplink", "toml"]


class AgentRequest(StrictModel):
    version: Literal[1]
    request_id: uuid.UUID
    managed_id: str = Field(pattern=MANAGED_ID.pattern)
    operation: Literal[
        "lock.acquire", "lock.release", "files.snapshot", "files.backup",
        "files.prepare_credentials", "files.commit_credentials",
        "files.restore_credentials", "files.cleanup", "service.status",
        "service.restart", "service.reload", "health.probe", "profile.export",
        "managed.describe",
    ]
    arguments: dict[str, object] = Field(default_factory=dict)


ARGUMENT_TYPES: dict[str, type[StrictModel]] = {
    "lock.acquire": OperationArguments,
    "lock.release": OperationArguments,
    "files.snapshot": OperationArguments,
    "files.backup": OperationArguments,
    "files.prepare_credentials": PrepareArguments,
    "files.commit_credentials": OperationArguments,
    "files.restore_credentials": OperationArguments,
    "files.cleanup": OperationArguments,
    "service.status": EmptyArguments,
    "service.restart": OperationArguments,
    "service.reload": OperationArguments,
    "health.probe": EmptyArguments,
    "profile.export": ExportArguments,
    "managed.describe": EmptyArguments,
}


class AgentResponse(StrictModel):
    version: Literal[1] = 1
    request_id: uuid.UUID
    ok: bool
    result: dict[str, object] | None = None
    error: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")


def decode_request(data: bytes) -> tuple[AgentRequest, StrictModel]:
    if len(data) > MAX_MESSAGE_BYTES:
        raise ProtocolError("request_too_large")
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ProtocolError("malformed_request")
        if value.get("version") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported_protocol_version")
        if value.get("operation") not in ARGUMENT_TYPES:
            raise ProtocolError("unknown_operation")
        request = AgentRequest.model_validate(value)
        arguments = ARGUMENT_TYPES[request.operation].model_validate(request.arguments)
    except ProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError):
        raise ProtocolError("malformed_request") from None
    return request, arguments


def encode_response(response: AgentResponse) -> bytes:
    payload = response.model_dump_json().encode()
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ProtocolError("response_too_large")
    return payload + b"\n"

