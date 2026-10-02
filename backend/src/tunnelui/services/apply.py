import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.trusttunnel import parse_credentials, source_hash
from tunnelui.system.files import atomic_replace, private_backup
from tunnelui.system.provider import FakeSystemProvider, SystemOperationError


@dataclass(frozen=True)
class ApplyResult:
    state: Literal["applied", "rolled_back", "rollback_failed"]
    operation_id: str
    hashes: dict[str, str]


class SandboxApplyService:
    """Phase 2 core exercise only; deliberately cannot accept a production provider.

    Fixed file map is supplied by test setup, never by an HTTP client. Before exposing
    to a root agent: durable journal, OS locks and no-follow descriptor traversal.
    """

    def __init__(self, files: dict[str, Path], backup_root: Path, provider: FakeSystemProvider):
        if type(provider) is not FakeSystemProvider:
            raise ValueError("Only FakeSystemProvider is permitted in sandbox apply")
        self.files, self.backup_root, self.provider = files, backup_root, provider
        self._lock = threading.Lock()
        self.events: list[dict[str, str]] = []

    def snapshot(self):
        for path in self.files.values():
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise DomainError("unsafe_managed_file")
        return {key: path.read_bytes() for key, path in self.files.items()}

    def apply_credentials(self, inbound_id: str, expected: dict[str, str], content: bytes):
        parse_credentials(content)
        with self._lock:
            original = self.snapshot()
            hashes = {key: source_hash(value) for key, value in original.items()}
            if hashes != expected:
                self.events.append({"action": "apply", "result": "drift_conflict"})
                raise DomainError("drift_conflict")
            operation_id = str(uuid.uuid4())
            private_backup(self.backup_root, operation_id, original)
            self.events.append({"action": "backup", "result": "success"})
            try:
                atomic_replace(self.files["credentials"], content)
                self.provider.restart(inbound_id)
                if not self.provider.health(inbound_id):
                    raise SystemOperationError("health_failed")
            except (OSError, SystemOperationError):
                try:
                    atomic_replace(self.files["credentials"], original["credentials"])
                    self.provider.restart(inbound_id)
                    if not self.provider.health(inbound_id):
                        raise SystemOperationError("rollback_health_failed")
                except (OSError, SystemOperationError):
                    state = "rollback_failed"
                else:
                    state = "rolled_back"
                self.events.append({"action": "rollback", "result": state})
                return ApplyResult(state, operation_id, hashes if state == "rolled_back" else {})
            hashes["credentials"] = source_hash(content)
            self.events.append({"action": "apply", "result": "success"})
            return ApplyResult("applied", operation_id, hashes)
