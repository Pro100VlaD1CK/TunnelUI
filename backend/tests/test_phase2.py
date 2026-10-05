import json

import pytest
from sqlalchemy import select

from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, AuditEvent, Client, Inbound, Operation
from tunnelui.services.coordinator import SimulatedCrash
from tunnelui.services.operations import safe_error_code, transition


def adopt(http):
    discovered = http.get("/api/trusttunnel/discovery")
    assert discovered.status_code == 200
    preview = http.post("/api/trusttunnel/adoption/preview").json()
    assert "fixture-password" not in json.dumps(preview)
    response = http.post(
        "/api/trusttunnel/adoption/confirm", json={"preview_id": preview["preview_id"]}
    )
    assert response.status_code == 201
    return response.json()["inbound_id"]


def create_client(http, username="phase2-user"):
    response = http.post("/api/clients", json={
        "username": username, "display_name": "Phase 2 user", "enabled": True,
        "comment": "", "expires_at": None,
    })
    assert response.status_code == 201
    return response.json()


def attach(http, client_id, inbound_id, key="phase2-attach-key"):
    return http.post(f"/api/clients/{client_id}/attachments", json={
        "inbound_id": inbound_id, "password": "new-fixture-password",
        "max_http2_conns": 3, "max_http3_conns": None, "idempotency_key": key,
    })


def test_adoption_attachment_apply_and_operation_audit_separation(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    page = sandbox_authenticated.get("/api/clients").json()
    assert page["items"][0]["username"] == "sandbox-alice"
    assert page["items"][0]["attachments"][0]["sync_state"] == "active"
    client = create_client(sandbox_authenticated)
    response = attach(sandbox_authenticated, client["id"], inbound_id)
    assert response.status_code == 201
    assert response.json()["operation"]["state"] == "succeeded"
    assert b"phase2-user" in sandbox_app.state.sandbox.environment.files["credentials"].read_bytes()
    with sandbox_app.state.sessions() as db:
        operation = db.scalar(select(Operation).order_by(Operation.created_at.desc()))
        audit = db.scalars(select(AuditEvent)).all()
        assert operation.state == "succeeded"
        assert "password" not in str(operation.__dict__).lower()
        assert all("new-fixture-password" not in item.summary for item in audit)
        assert {item.action for item in audit} >= {"attachment.create", "config.apply"}


def test_attachment_disable_detach_and_last_active_protection(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_client(sandbox_authenticated)
    result = attach(sandbox_authenticated, client["id"], inbound_id).json()
    attachment_id = result["attachment_id"]
    response = sandbox_authenticated.put(f"/api/attachments/{attachment_id}", json={
        "enabled": False, "revision": 1, "idempotency_key": "disable-second-client",
    })
    assert response.json()["operation"]["state"] == "succeeded"
    response = sandbox_authenticated.request("DELETE", f"/api/attachments/{attachment_id}", json={
        "revision": 2, "idempotency_key": "detach-second-client",
    })
    assert response.status_code == 200
    with sandbox_app.state.sessions() as db:
        assert db.get(Attachment, attachment_id) is None
        imported = db.scalar(select(Attachment))
        imported_id, revision = imported.id, imported.revision
    response = sandbox_authenticated.put(f"/api/attachments/{imported_id}", json={
        "enabled": False, "revision": revision, "idempotency_key": "disable-last-client",
    })
    assert response.status_code == 409
    assert response.json()["code"] == "empty_credentials_unsupported_v1_1_0"


@pytest.mark.parametrize(
    ("checkpoint", "checkpoint_state", "expected"),
    [
        ("before_backup", "pending", "failed"),
        ("after_preparing", "preparing", "failed"),
        ("after_backup", "backed_up", "failed"),
        ("after_temp_write", "writing", "failed"),
        ("after_replace", "applying", "rolled_back"),
        ("after_restart", "checking", "rolled_back"),
        ("during_health", "checking", "rolled_back"),
    ],
)
def test_interruption_checkpoints_recover_idempotently(
    sandbox_authenticated, sandbox_app, checkpoint, checkpoint_state, expected
):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    with sandbox_app.state.sessions() as db:
        client = Client(username=f"crash-{checkpoint}", display_name="Crash fixture")
        db.add(client)
        db.flush()
        db.add(Attachment(
            client_id=client.id, inbound_id=inbound_id,
            secret_ciphertext=runtime.coordinator.box.encrypt("crash-fixture-password"),
            desired_state="active", applied_state="pending", sync_state="pending",
        ))
        db.commit()
    original = runtime.environment.files["credentials"].read_bytes()
    runtime.coordinator.interrupt_at = checkpoint
    with pytest.raises(SimulatedCrash):
        runtime.coordinator.apply(inbound_id, f"interrupt-{checkpoint}", "admin")
    with sandbox_app.state.sessions() as db:
        operation = db.scalar(select(Operation).where(
            Operation.idempotency_key == f"interrupt-{checkpoint}"
        ))
        operation_id = operation.id
        assert operation.state == checkpoint_state
    first = runtime.coordinator.recover(operation_id)
    second = runtime.coordinator.recover(operation_id)
    assert first["state"] == second["state"] == expected
    assert runtime.environment.files["credentials"].read_bytes() == original


def test_interruption_during_rollback_and_rollback_failure(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    with sandbox_app.state.sessions() as db:
        imported = db.scalar(select(Client).where(Client.username == "sandbox-alice"))
        imported.display_name = "changed without credential impact"
        client = Client(username="rollback-user", display_name="Rollback")
        db.add(client)
        db.flush()
        db.add(Attachment(
            client_id=client.id, inbound_id=inbound_id,
            secret_ciphertext=runtime.coordinator.box.encrypt("rollback-fixture-password"),
            desired_state="active", applied_state="pending", sync_state="pending",
        ))
        db.commit()
    runtime.provider.configure("health_failure_rollback_success")
    runtime.coordinator.interrupt_at = "during_rollback"
    with pytest.raises(SimulatedCrash):
        runtime.coordinator.apply(inbound_id, "interrupt-during-rollback", "admin")
    with sandbox_app.state.sessions() as db:
        operation = db.scalar(select(Operation).where(
            Operation.idempotency_key == "interrupt-during-rollback"
        ))
    assert runtime.coordinator.recover(operation.id)["state"] == "rolled_back"
    runtime.provider.configure("rollback_failure")
    result = runtime.coordinator.apply(inbound_id, "rollback-failure-key", "admin")
    assert result["state"] == "needs_recovery"
    with sandbox_app.state.sessions() as db:
        assert db.get(Inbound, inbound_id).config_state == "recovery_required"


def test_concurrent_apply_returns_conflict(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    with runtime.locks.acquire(inbound_id), pytest.raises(DomainError, match="operation_in_progress"):
        runtime.coordinator.apply(inbound_id, "concurrent-apply-key", "admin")


def test_drift_is_rechecked_and_pending_attachment_is_not_active(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_client(sandbox_authenticated, "drift-user")
    sandbox_app.state.sandbox.environment.files["rules"].write_text("# external change")
    response = attach(sandbox_authenticated, client["id"], inbound_id, "drift-attach-key")
    assert response.status_code == 409 and response.json()["code"] == "drift_conflict"
    with sandbox_app.state.sessions() as db:
        attachment = db.scalar(select(Attachment).where(Attachment.client_id == client["id"]))
        assert attachment.sync_state == "conflict"
        assert attachment.applied_state == "pending"


def test_global_disable_rollback_keeps_effective_access_visible_as_error(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_client(sandbox_authenticated, "disable-rollback")
    assert attach(
        sandbox_authenticated, client["id"], inbound_id, "disable-rollback-attach"
    ).json()["operation"]["state"] == "succeeded"
    sandbox_app.state.sandbox.provider.configure("health_failure_rollback_success")
    response = sandbox_authenticated.put(f"/api/clients/{client['id']}/access", json={
        "enabled": False, "revision": 1,
        "idempotency_key": "global-disable-rollback",
    })
    assert response.status_code == 200
    assert response.json()["operations"][0]["state"] == "rolled_back"
    item = next(
        row for row in sandbox_authenticated.get("/api/clients?limit=100").json()["items"]
        if row["id"] == client["id"]
    )
    assert item["enabled"] is False
    assert item["attachments"][0]["applied_state"] == "active"
    assert item["attachments"][0]["sync_state"] == "error"


def test_state_machine_and_error_sanitization(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    with sandbox_app.state.sessions() as db:
        operation = Operation(
            inbound_id=inbound_id, kind="test", state="pending",
            idempotency_key="state-machine-key", request_fingerprint="0" * 64,
            expected_hashes_json="{}", result_hashes_json="{}",
        )
        db.add(operation)
        db.commit()
        transition(db, operation, "preparing")
        with pytest.raises(DomainError, match="invalid_operation_transition"):
            transition(db, operation, "succeeded")
    assert safe_error_code("password=secret") == "operation_failed"
    assert safe_error_code("health_failed") == "health_failed"
    columns = set(Operation.__table__.columns.keys())
    assert not columns & {"password", "credentials", "content", "deeplink", "api_token", "private_key"}


def test_sandbox_management_fails_closed_without_explicit_mode(authenticated):
    response = authenticated.get("/api/trusttunnel/discovery")
    assert response.status_code == 503
    assert response.json() == {"code": "sandbox_unavailable"}


def test_fake_profile_exports_are_explicit_and_secret_free(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    with sandbox_app.state.sessions() as db:
        client_id = db.scalar(select(Client.id).where(Client.username == "sandbox-alice"))
    deep = sandbox_authenticated.post(
        f"/api/clients/{client_id}/profiles/trusttunnel",
        json={"format": "deeplink", "inbound_id": inbound_id},
    ).json()
    config = sandbox_authenticated.post(
        f"/api/clients/{client_id}/profiles/trusttunnel",
        json={"format": "toml", "inbound_id": inbound_id},
    ).json()
    assert inbound_id
    assert deep["sandbox"] is True and deep["content"].startswith("sandbox-profile:")
    assert config["sandbox"] is True and 'server = "vpn.sandbox.invalid:443"' in config["content"]
    assert "fixture-password-not-for-production" not in json.dumps([deep, config])
    wrong_inbound = sandbox_authenticated.post(
        f"/api/clients/{client_id}/profiles/trusttunnel",
        json={"format": "deeplink", "inbound_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert wrong_inbound.status_code == 404


def test_attached_client_allows_metadata_edit_but_blocks_credential_changes(
    sandbox_authenticated, sandbox_app
):
    adopt(sandbox_authenticated)
    clients = sandbox_authenticated.get("/api/clients?limit=100").json()["items"]
    client = next(item for item in clients if item["username"] == "sandbox-alice")
    metadata = sandbox_authenticated.put(
        f"/api/clients/{client['id']}",
        json={
            "username": client["username"], "display_name": "Alice renamed",
            "enabled": client["enabled"], "comment": "safe metadata",
            "expires_at": client["expires_at"], "revision": client["revision"],
        },
    )
    assert metadata.status_code == 200
    changed = metadata.json()
    assert changed["display_name"] == "Alice renamed"
    blocked = sandbox_authenticated.put(
        f"/api/clients/{client['id']}",
        json={
            "username": "renamed-login", "display_name": changed["display_name"],
            "enabled": changed["enabled"], "comment": changed["comment"],
            "expires_at": changed["expires_at"], "revision": changed["revision"],
        },
    )
    assert blocked.status_code == 409
    assert blocked.json() == {"code": "attached_client_requires_apply"}
