from sqlalchemy import select

from tunnelui.models import Attachment, AuditEvent, Inbound

BODY = {"username": "ivan", "display_name": "Иван", "comment": "Private comment"}


def test_crud_duplicate_revision_audit(authenticated, app):
    created = authenticated.post("/api/clients", json=BODY)
    assert created.status_code == 201
    client = created.json()
    assert "password" not in client
    assert authenticated.post("/api/clients", json=BODY).status_code == 409
    update = {**BODY, "enabled": False, "revision": 1}
    response = authenticated.put(f"/api/clients/{client['id']}", json=update)
    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert authenticated.put(f"/api/clients/{client['id']}", json=update).status_code == 409
    page = authenticated.get("/api/clients?q=ivan&enabled=false").json()
    assert page["total"] == 1
    assert authenticated.get("/api/clients?enabled=true").json()["total"] == 0
    assert authenticated.delete(f"/api/clients/{client['id']}?revision=1").status_code == 409
    assert authenticated.delete(f"/api/clients/{client['id']}?revision=2").status_code == 204
    with app.state.sessions() as db:
        rows = db.scalars(select(AuditEvent)).all()
        assert {r.action for r in rows} >= {"client.create", "client.disable", "client.delete"}
        assert all("Private comment" not in r.summary for r in rows)


def test_attached_changes_fail_closed(authenticated, app):
    client = authenticated.post("/api/clients", json=BODY).json()
    with app.state.sessions() as db:
        inbound = Inbound(name="Fixture", kind="trusttunnel")
        db.add(inbound)
        db.flush()
        db.add(Attachment(client_id=client["id"], inbound_id=inbound.id))
        db.commit()
    assert authenticated.delete(f"/api/clients/{client['id']}?revision=1").json()["code"] == "attached_client_requires_apply"
    assert authenticated.put(
        f"/api/clients/{client['id']}",
        json={**BODY, "username": "changed-username", "revision": 1},
    ).status_code == 409


def test_pagination_and_validation(authenticated):
    assert authenticated.post("/api/clients", json={**BODY, "password": "secret"}).status_code == 422
    for i in range(3):
        assert authenticated.post("/api/clients", json={**BODY, "username": f"user{i}"}).status_code == 201
    response = authenticated.get("/api/clients?limit=2&offset=2").json()
    assert response["total"] == 3 and len(response["items"]) == 1
    assert authenticated.get("/api/clients?limit=1000").status_code == 422
