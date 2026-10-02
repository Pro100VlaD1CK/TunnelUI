import secrets

from fastapi import Depends, Request

from tunnelui.domain.errors import DomainError
from tunnelui.models import Admin
from tunnelui.services.auth import find_session

COOKIE = "tunnelui_session"


def db_session(request: Request):
    with request.app.state.sessions() as db:
        yield db


def session(request: Request, db=Depends(db_session)):
    row = find_session(db, request.cookies.get(COOKIE))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        token = request.headers.get("x-csrf-token", "")
        if origin != request.app.state.settings.origin or not secrets.compare_digest(
            token, row.csrf_token
        ):
            raise DomainError("csrf_failed", 403)
    return row


def admin(row=Depends(session), db=Depends(db_session)):
    identity = db.get(Admin, row.admin_id) if row.admin_id else None
    if identity is None:
        raise DomainError("authentication_required", 401)
    return identity
