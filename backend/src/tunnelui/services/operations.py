import json
import re
import threading
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session

from tunnelui.domain.errors import DomainError
from tunnelui.models import Operation, now

TERMINAL_STATES = {"succeeded", "rolled_back", "failed", "needs_recovery"}
INCOMPLETE_STATES = {
    "pending", "preparing", "backed_up", "writing", "applying", "checking",
    "rolling_back",
}
TRANSITIONS = {
    "pending": {"preparing", "failed"},
    "preparing": {"backed_up", "applying", "failed"},
    "backed_up": {"writing", "failed", "needs_recovery"},
    "writing": {"applying", "failed", "needs_recovery"},
    "applying": {"checking", "rolling_back", "failed", "needs_recovery"},
    "checking": {"succeeded", "rolling_back", "needs_recovery"},
    "rolling_back": {"rolled_back", "needs_recovery"},
}
SAFE_ERROR = re.compile(r"^[a-z0-9_]{1,64}$")


def safe_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    return value if SAFE_ERROR.fullmatch(value) else "operation_failed"


def transition(db: Session, operation: Operation, state: str,
               error_code: str | None = None) -> None:
    if state not in TRANSITIONS.get(operation.state, set()):
        raise DomainError("invalid_operation_transition")
    operation.state = state
    operation.error_code = safe_error_code(error_code)
    operation.updated_at = now()
    if state in TERMINAL_STATES:
        operation.completed_at = operation.updated_at
    db.commit()


def operation_output(operation: Operation) -> dict:
    """Return a deliberately fixed, secret-free technical view."""
    return {key: getattr(operation, key) for key in (
        "id", "inbound_id", "kind", "state", "error_code", "created_at",
        "updated_at", "completed_at",
    )}


class InboundLocks:
    """Process-local sandbox locks. Production additionally requires OS locks in the agent."""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    @contextmanager
    def acquire(self, inbound_id: str):
        with self._guard:
            lock = self._locks.setdefault(inbound_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise DomainError("operation_in_progress")
        try:
            yield
        finally:
            lock.release()


def active_operation(db: Session, inbound_id: str) -> Operation | None:
    return db.scalar(select(Operation).where(
        Operation.inbound_id == inbound_id,
        Operation.state.in_(INCOMPLETE_STATES),
    ).order_by(Operation.created_at.desc()).limit(1))


def hashes(value: str) -> dict[str, str]:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        raise DomainError("invalid_operation_metadata")
    return parsed
