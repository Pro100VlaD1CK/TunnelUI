from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select

from tunnelui.api.dependencies import COOKIE, admin, db_session, session
from tunnelui.api.schemas import ClientInput, ClientOutput, ClientPage, ClientUpdate, LoginInput
from tunnelui.domain.errors import DomainError
from tunnelui.models import AuditEvent, Inbound
from tunnelui.repositories.clients import ClientRepository
from tunnelui.services import auth
from tunnelui.services.audit import record
from tunnelui.services.clients import ClientService

router = APIRouter(prefix="/api")


def set_cookie(response: Response, request: Request, token: str, age: int):
    response.set_cookie(COOKIE, token, max_age=age, httponly=True,
                        secure=request.app.state.settings.secure_cookie,
                        samesite="strict", path="/")


@router.get("/health")
def health():
    return {"status": "ok", "host_control": "unavailable"}


@router.get("/auth/csrf")
def csrf(request: Request, response: Response, db=Depends(db_session)):
    try:
        row = auth.find_session(db, request.cookies.get(COOKIE))
    except DomainError:
        token, row = auth.new_session(db, None, 600)
        db.commit()
        set_cookie(response, request, token, 600)
    return {"csrf_token": row.csrf_token}


@router.post("/auth/login")
def login(data: LoginInput, request: Request, response: Response,
          row=Depends(session), db=Depends(db_session)):
    request.app.state.limiter.check(request.client.host if request.client else "unknown")
    age = request.app.state.settings.session_seconds
    token, new_row, identity = auth.login(db, row, data.username,
                                         data.password.get_secret_value(), age)
    set_cookie(response, request, token, age)
    return {"username": identity.username, "csrf_token": new_row.csrf_token}


@router.get("/auth/me")
def me(identity=Depends(admin)):
    return {"username": identity.username}


@router.post("/auth/logout", status_code=204)
def logout(response: Response, identity=Depends(admin), row=Depends(session), db=Depends(db_session)):
    db.delete(row)
    record(db, identity.username, "auth.logout", "admin", str(identity.id))
    db.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/clients", response_model=ClientPage)
def clients(q: str = Query("", max_length=160), enabled: bool | None = None,
            offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
            _=Depends(admin), db=Depends(db_session)):
    rows, total = ClientRepository(db).page(q, enabled, offset, limit)
    return {"items": rows, "total": total}


@router.post("/clients", response_model=ClientOutput, status_code=201)
def create_client(data: ClientInput, identity=Depends(admin), db=Depends(db_session)):
    return ClientService(db, identity.username).create(data)


@router.put("/clients/{client_id}", response_model=ClientOutput)
def edit_client(client_id: str, data: ClientUpdate, identity=Depends(admin), db=Depends(db_session)):
    return ClientService(db, identity.username).edit(client_id, data)


@router.delete("/clients/{client_id}", status_code=204)
def delete_client(client_id: str, revision: int = Query(ge=1),
                  identity=Depends(admin), db=Depends(db_session)):
    ClientService(db, identity.username).remove(client_id, revision)


@router.get("/inbounds")
def inbounds(_=Depends(admin), db=Depends(db_session)):
    rows = db.scalars(select(Inbound).order_by(Inbound.name)).all()
    return {"items": [{"id": r.id, "name": r.name, "kind": r.kind,
                       "enabled": r.enabled} for r in rows], "adoption_available": False}


@router.get("/audit")
def audit(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
          _=Depends(admin), db=Depends(db_session)):
    rows = db.scalars(select(AuditEvent).order_by(
        AuditEvent.timestamp.desc(), AuditEvent.id
    ).offset(offset).limit(limit)).all()
    return {"items": [{key: getattr(row, key) for key in (
        "id", "timestamp", "admin", "action", "entity_type", "entity_id", "result", "summary"
    )} for row in rows], "total": db.scalar(select(func.count()).select_from(AuditEvent))}
