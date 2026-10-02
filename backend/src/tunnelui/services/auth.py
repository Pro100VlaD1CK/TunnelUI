import secrets
import threading
import time
from collections import deque

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tunnelui.domain.errors import DomainError
from tunnelui.models import Admin, AdminSession
from tunnelui.security import digest, password_hasher, verify_password
from tunnelui.services.audit import record

DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


class LoginLimiter:
    """Single-process bounded limiter; ignores untrusted forwarding headers."""

    def __init__(self):
        self._attempts: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, address: str):
        now = time.monotonic()
        with self._lock:
            for key in list(self._attempts):
                if not self._attempts[key] or self._attempts[key][-1] < now - 300:
                    del self._attempts[key]
            if address not in self._attempts and len(self._attempts) >= 4096:
                raise DomainError("login_rate_limited", 429)
            queue = self._attempts.setdefault(address, deque())
            while queue and queue[0] < now - 300:
                queue.popleft()
            if len(queue) >= 5:
                raise DomainError("login_rate_limited", 429)
            queue.append(now)


def new_session(db: Session, admin_id: int | None, lifetime: int):
    token = secrets.token_urlsafe(32)
    row = AdminSession(token_hash=digest(token), admin_id=admin_id,
                       csrf_token=secrets.token_urlsafe(32),
                       expires_at=int(time.time()) + lifetime)
    db.execute(delete(AdminSession).where(AdminSession.expires_at <= int(time.time())))
    db.add(row)
    return token, row


def find_session(db: Session, token: str | None):
    row = db.get(AdminSession, digest(token)) if token else None
    if row is None or row.expires_at <= time.time():
        raise DomainError("session_expired", 401)
    return row


def login(db: Session, current: AdminSession, username: str, password: str, lifetime: int):
    admin = db.scalar(select(Admin).where(Admin.username == username))
    valid = verify_password(admin.password_hash if admin else DUMMY_HASH, password)
    if not admin or not valid:
        raise DomainError("invalid_credentials", 401)
    db.delete(current)
    token, row = new_session(db, admin.id, lifetime)
    record(db, admin.username, "auth.login", "admin", str(admin.id))
    db.commit()
    return token, row, admin
