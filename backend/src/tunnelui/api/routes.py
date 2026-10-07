import json

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select

from tunnelui.api.dependencies import COOKIE, admin, db_session, session
from tunnelui.api.schemas import (
    AdoptionConfirm,
    AttachmentCreate,
    AttachmentDetach,
    AttachmentState,
    ClientAccess,
    ClientInput,
    ClientOutput,
    ClientUpdate,
    ExportRequest,
    FakeScenario,
    IdempotentAction,
    LoginInput,
)
from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, AuditEvent, Client, Inbound, Operation, now
from tunnelui.repositories.clients import ClientRepository
from tunnelui.services import auth
from tunnelui.services.attachments import AttachmentService
from tunnelui.services.audit import record
from tunnelui.services.clients import ClientService
from tunnelui.services.operations import active_operation, operation_output
from tunnelui.system.provider import SystemOperationError

router = APIRouter(prefix="/api")


def management(request: Request):
    runtime = request.app.state.management
    if runtime is None:
        raise DomainError("sandbox_unavailable", 503)
    return runtime


def sandbox(request: Request):
    runtime = request.app.state.sandbox
    if runtime is None:
        raise DomainError("sandbox_unavailable", 503)
    return runtime


def adoption(request: Request):
    runtime = management(request)
    service = getattr(runtime, "adoption", None)
    if service is None:
        raise DomainError("management_unavailable", 503)
    return service


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


@router.get("/clients")
def clients(q: str = Query("", max_length=160), enabled: bool | None = None,
            offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
            _=Depends(admin), db=Depends(db_session)):
    rows, total = ClientRepository(db).page(q, enabled, offset, limit)
    attachments = AttachmentService.summaries(db, [row.id for row in rows])
    return {"items": [{
        **{key: getattr(row, key) for key in (
            "id", "username", "display_name", "enabled", "comment", "expires_at",
            "created_at", "updated_at", "revision",
        )},
        "attachments": attachments.get(row.id, []),
    } for row in rows], "total": total}


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


@router.put("/clients/{client_id}/access")
def client_access(client_id: str, data: ClientAccess, request: Request,
                  identity=Depends(admin), db=Depends(db_session)):
    runtime = management(request)
    client = db.get(Client, client_id)
    if not client:
        raise DomainError("client_not_found", 404)
    if client.revision != data.revision:
        raise DomainError("revision_conflict")
    previous = client.enabled
    client.enabled = data.enabled
    client.revision += 1
    client.updated_at = now()
    rows = db.scalars(select(Attachment).where(Attachment.client_id == client_id)).all()
    inbound_ids = sorted({row.inbound_id for row in rows})
    for row in rows:
        if row.desired_state == "active":
            row.sync_state = "pending"
    try:
        for inbound_id in inbound_ids:
            runtime.coordinator._render(db, inbound_id)
    except DomainError:
        client.enabled = previous
        db.rollback()
        raise
    record(db, identity.username,
           "client.edit" if data.enabled else "client.disable", "client", client_id)
    db.commit()
    operations = []
    for index, inbound_id in enumerate(inbound_ids):
        operations.append(runtime.coordinator.apply(
            inbound_id, f"{data.idempotency_key}-{index}", identity.username,
            "client_access",
        ))
    return {"client_id": client_id, "enabled": data.enabled, "operations": operations}


@router.get("/inbounds")
def inbounds(request: Request, _=Depends(admin), db=Depends(db_session)):
    rows = db.scalars(select(Inbound).order_by(Inbound.name)).all()
    runtime = request.app.state.management
    items = []
    for row in rows:
        metadata = json.loads(row.metadata_json or "{}")
        count = db.scalar(select(func.count()).select_from(Attachment).where(
            Attachment.inbound_id == row.id
        ))
        current = active_operation(db, row.id)
        service_status = "unknown"
        if runtime and row.registry_id == runtime.environment.registry_id:
            if hasattr(runtime.provider, "running"):
                service_status = "running" if runtime.provider.running else "stopped"
            else:
                try:
                    service_status = "running" if runtime.provider.status()["running"] else "stopped"
                except SystemOperationError:
                    service_status = "unknown"
        items.append({
            "id": row.id, "name": row.name, "kind": row.kind, "enabled": row.enabled,
            "version": metadata.get("version"), "listen_address": metadata.get("listen_address"),
            "public_address": row.public_address, "protocols": metadata.get("protocols", []),
            "client_count": count, "config_state": "pending" if current else row.config_state,
            "service_status": service_status,
            "operation": operation_output(current) if current else None,
        })
    adoption_available = bool(
        runtime
        and getattr(runtime, "adoption", None)
        and not any(
            row.registry_id == runtime.environment.registry_id
            for row in rows
        )
    )
    return {"items": items, "adoption_available": adoption_available}


@router.get("/inbounds/{inbound_id}")
def inbound_detail(inbound_id: str, request: Request, _=Depends(admin), db=Depends(db_session)):
    row = db.get(Inbound, inbound_id)
    if not row:
        raise DomainError("inbound_not_found", 404)
    metadata = json.loads(row.metadata_json or "{}")
    attachments = db.execute(select(Attachment, Client).join(
        Client, Client.id == Attachment.client_id
    ).where(Attachment.inbound_id == inbound_id).order_by(Client.username)).all()
    operations = db.scalars(select(Operation).where(
        Operation.inbound_id == inbound_id
    ).order_by(Operation.created_at.desc()).limit(20)).all()
    runtime = request.app.state.management
    service_status = "unknown"
    if runtime and row.registry_id == runtime.environment.registry_id:
        if hasattr(runtime.provider, "running"):
            service_status = "running" if runtime.provider.running else "stopped"
        else:
            try:
                service_status = "running" if runtime.provider.status()["running"] else "stopped"
            except SystemOperationError:
                service_status = "unknown"
    return {
        "id": row.id, "name": row.name, "enabled": row.enabled,
        "kind": row.kind, "public_address": row.public_address,
        "config_state": row.config_state, "metadata": metadata,
        "service_status": service_status,
        "attachments": [{
            "id": attachment.id, "client_id": client.id, "username": client.username,
            "display_name": client.display_name, "enabled": attachment.enabled,
            "desired_state": attachment.desired_state,
            "applied_state": attachment.applied_state,
            "sync_state": attachment.sync_state, "revision": attachment.revision,
            "max_http2_conns": attachment.max_http2_conns,
            "max_http3_conns": attachment.max_http3_conns,
        } for attachment, client in attachments],
        "operations": [operation_output(item) for item in operations],
    }


@router.get("/trusttunnel/discovery")
def trusttunnel_discovery(request: Request, _=Depends(admin)):
    return adoption(request).discover()


@router.post("/trusttunnel/adoption/preview")
def trusttunnel_preview(request: Request, _=Depends(admin)):
    return adoption(request).detect()


@router.post("/trusttunnel/adoption/confirm", status_code=201)
def trusttunnel_confirm(data: AdoptionConfirm, request: Request,
                        identity=Depends(admin), db=Depends(db_session)):
    service = adoption(request)
    runtime = management(request)
    return service.confirm(
        data.preview_id, db, runtime.coordinator.box, identity.username
    )


@router.post("/clients/{client_id}/attachments", status_code=201)
def attach_client(client_id: str, data: AttachmentCreate, request: Request,
                  identity=Depends(admin), db=Depends(db_session)):
    runtime = management(request)
    attachment_id, operation = AttachmentService(
        db, identity.username, runtime.coordinator.box, runtime.coordinator
    ).create(
        client_id, data.inbound_id, data.password.get_secret_value(),
        data.max_http2_conns, data.max_http3_conns, data.idempotency_key,
    )
    return {"attachment_id": attachment_id, "operation": operation}


@router.put("/attachments/{attachment_id}")
def update_attachment(attachment_id: str, data: AttachmentState, request: Request,
                      identity=Depends(admin), db=Depends(db_session)):
    runtime = management(request)
    operation = AttachmentService(
        db, identity.username, runtime.coordinator.box, runtime.coordinator
    ).set_enabled(attachment_id, data.enabled, data.revision, data.idempotency_key)
    return {"operation": operation}


@router.delete("/attachments/{attachment_id}")
def detach_client(attachment_id: str, data: AttachmentDetach, request: Request,
                  identity=Depends(admin), db=Depends(db_session)):
    runtime = management(request)
    operation = AttachmentService(
        db, identity.username, runtime.coordinator.box, runtime.coordinator
    ).detach(attachment_id, data.revision, data.idempotency_key)
    return {"operation": operation}


@router.post("/inbounds/{inbound_id}/drift")
def check_drift(inbound_id: str, request: Request, _=Depends(admin)):
    return management(request).coordinator.check_drift(inbound_id)


@router.post("/inbounds/{inbound_id}/cancel-pending", status_code=204)
def cancel_pending(inbound_id: str, request: Request, _=Depends(admin)):
    management(request).coordinator.cancel_pending(inbound_id)


@router.post("/inbounds/{inbound_id}/reimport")
def reimport_inbound(inbound_id: str, request: Request, identity=Depends(admin)):
    return management(request).coordinator.reimport(inbound_id, identity.username)


@router.post("/inbounds/{inbound_id}/restart")
def restart_inbound(inbound_id: str, data: IdempotentAction, request: Request,
                    identity=Depends(admin)):
    return {"operation": management(request).coordinator.restart(
        inbound_id, data.idempotency_key, identity.username
    )}


@router.post("/operations/{operation_id}/recover")
def recover_operation(operation_id: str, request: Request, identity=Depends(admin)):
    return {"operation": management(request).coordinator.recover(
        operation_id, identity.username
    )}


@router.delete("/inbounds/{inbound_id}/management", status_code=204)
def unmanage_inbound(inbound_id: str, identity=Depends(admin), db=Depends(db_session)):
    row = db.get(Inbound, inbound_id)
    if not row:
        raise DomainError("inbound_not_found", 404)
    if active_operation(db, inbound_id):
        raise DomainError("operation_in_progress")
    row.enabled = False
    row.config_state = "unmanaged"
    row.updated_at = now()
    record(db, identity.username, "inbound.unmanage", "inbound", inbound_id)
    db.commit()


@router.post("/clients/{client_id}/profiles/trusttunnel")
def export_profile(client_id: str, data: ExportRequest, request: Request,
                   _=Depends(admin), db=Depends(db_session)):
    runtime = management(request)
    row = db.execute(select(Client, Attachment, Inbound).join(
        Attachment, Attachment.client_id == Client.id
    ).join(Inbound, Inbound.id == Attachment.inbound_id).where(
        Client.id == client_id, Inbound.id == data.inbound_id,
        Attachment.applied_state == "active", Inbound.enabled.is_(True)
    )).first()
    if not row:
        raise DomainError("active_profile_not_found", 404)
    client, _attachment, inbound = row
    exported = runtime.exporter.export(client.username, inbound.public_address or "", data.format)
    return {"format": exported.format, "content": exported.content,
            "media_type": exported.media_type, "sandbox": request.app.state.sandbox is not None}


@router.post("/dev/fake-scenario", status_code=204)
def fake_scenario(data: FakeScenario, request: Request, _=Depends(admin)):
    runtime = sandbox(request)
    if data.scenario == "drift":
        path = runtime.environment.files["rules"]
        path.write_bytes(path.read_bytes() + b"\n# external sandbox drift\n")
    else:
        runtime.provider.configure(data.scenario)


@router.get("/audit")
def audit(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
          _=Depends(admin), db=Depends(db_session)):
    rows = db.scalars(select(AuditEvent).order_by(
        AuditEvent.timestamp.desc(), AuditEvent.id
    ).offset(offset).limit(limit)).all()
    return {"items": [{key: getattr(row, key) for key in (
        "id", "timestamp", "admin", "action", "entity_type", "entity_id", "result", "summary"
    )} for row in rows], "total": db.scalar(select(func.count()).select_from(AuditEvent))}
