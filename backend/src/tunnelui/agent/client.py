import base64
import json
import socket
import threading
import uuid
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from tunnelui.agent.protocol import (
    MAX_MESSAGE_BYTES,
    MAX_RESPONSE_BYTES,
    PROTOCOL_VERSION,
    AgentResponse,
)


class AgentClientError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class AgentTransport(Protocol):
    def exchange(self, payload: bytes) -> bytes: ...


class UnixSocketTransport:
    def __init__(self, socket_path: Path, timeout: float = 20.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def exchange(self, payload: bytes) -> bytes:
        if len(payload) > MAX_MESSAGE_BYTES:
            raise AgentClientError("request_too_large")
        if not hasattr(socket, "AF_UNIX"):
            raise AgentClientError("linux_agent_unavailable")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(str(self.socket_path))
                connection.sendall(payload + b"\n")
                chunks = []
                total = 0
                while True:
                    chunk = connection.recv(min(65536, MAX_RESPONSE_BYTES + 1 - total))
                    if not chunk:
                        raise AgentClientError("agent_connection_closed")
                    marker = chunk.find(b"\n")
                    if marker >= 0:
                        total += marker
                        if total > MAX_RESPONSE_BYTES:
                            raise AgentClientError("response_too_large")
                        chunks.append(chunk[:marker])
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise AgentClientError("response_too_large")
        except (OSError, TimeoutError):
            raise AgentClientError("agent_unavailable") from None
        return b"".join(chunks)


class LinuxAgentClient:
    def __init__(self, transport: AgentTransport):
        self.transport = transport
        self._guard = threading.Lock()
        self._operations: dict[str, str] = {}

    def begin(self, managed_id: str, operation_id: str) -> None:
        self._call(managed_id, "lock.acquire", {"operation_id": operation_id})
        with self._guard:
            self._operations[managed_id] = operation_id

    def end(self, managed_id: str, operation_id: str) -> None:
        try:
            self._call(managed_id, "lock.release", {"operation_id": operation_id})
        finally:
            with self._guard:
                if self._operations.get(managed_id) == operation_id:
                    self._operations.pop(managed_id)

    def snapshot(self, managed_id: str) -> dict[str, bytes]:
        temporary = False
        operation_id = self._active(managed_id)
        if operation_id is None:
            operation_id = str(uuid.uuid4())
            self.begin(managed_id, operation_id)
            temporary = True
        try:
            result = self._call(
                managed_id, "files.snapshot", {"operation_id": operation_id}
            )
            encoded = result.get("files")
            if not isinstance(encoded, dict):
                raise AgentClientError("malformed_agent_response")
            return {str(key): _decode(value) for key, value in encoded.items()}
        finally:
            if temporary:
                self.end(managed_id, operation_id)

    def backup(self, managed_id: str, operation_id: str) -> None:
        self._operation(managed_id, "files.backup", operation_id)

    def prepare_credentials(self, managed_id: str, operation_id: str, content: bytes) -> None:
        self._call(managed_id, "files.prepare_credentials", {
            "operation_id": operation_id,
            "content_b64": base64.b64encode(content).decode(),
        })

    def commit_credentials(
        self, managed_id: str, operation_id: str, expected_hashes: dict[str, str]
    ) -> None:
        self._call(managed_id, "files.commit_credentials", {
            "operation_id": operation_id, "expected_hashes": expected_hashes,
        })

    def restore_credentials(self, managed_id: str, operation_id: str) -> None:
        self._operation(managed_id, "files.restore_credentials", operation_id)

    def cleanup(self, managed_id: str, operation_id: str) -> None:
        self._operation(managed_id, "files.cleanup", operation_id)

    def describe(self, managed_id: str) -> dict[str, object]:
        return self._call(managed_id, "managed.describe", {})

    def status(self, managed_id: str) -> dict[str, object]:
        return self._call(managed_id, "service.status", {})

    def restart(self, managed_id: str) -> None:
        self._operation(managed_id, "service.restart", self._required(managed_id))

    def reload(self, managed_id: str) -> None:
        self._operation(managed_id, "service.reload", self._required(managed_id))

    def health(self, managed_id: str) -> dict[str, object]:
        return self._call(managed_id, "health.probe", {})

    def export(self, managed_id: str, username: str, format: str) -> bytes:
        result = self._call(
            managed_id, "profile.export", {"username": username, "format": format}
        )
        return _decode(result.get("content_b64"))

    def _operation(self, managed_id: str, operation: str, operation_id: str) -> None:
        self._call(managed_id, operation, {"operation_id": operation_id})

    def _required(self, managed_id: str) -> str:
        operation_id = self._active(managed_id)
        if operation_id is None:
            raise AgentClientError("operation_lock_required")
        return operation_id

    def _active(self, managed_id: str) -> str | None:
        with self._guard:
            return self._operations.get(managed_id)

    def _call(self, managed_id: str, operation: str, arguments: dict) -> dict[str, object]:
        request_id = uuid.uuid4()
        payload = json.dumps({
            "version": PROTOCOL_VERSION,
            "request_id": str(request_id),
            "managed_id": managed_id,
            "operation": operation,
            "arguments": arguments,
        }, separators=(",", ":")).encode()
        try:
            response = AgentResponse.model_validate_json(self.transport.exchange(payload))
        except (ValidationError, ValueError):
            raise AgentClientError("malformed_agent_response") from None
        if response.request_id != request_id:
            raise AgentClientError("agent_request_mismatch")
        if not response.ok:
            raise AgentClientError(response.error or "agent_failure")
        return response.result or {}


def _decode(value) -> bytes:
    if not isinstance(value, str):
        raise AgentClientError("malformed_agent_response")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        raise AgentClientError("malformed_agent_response") from None

