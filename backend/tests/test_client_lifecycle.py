import json
import shutil

from sqlalchemy import select

from tunnelui.integrations.trusttunnel import source_hash
from tunnelui.models import (
    Attachment,
    AttachmentIntent,
    Client,
    ClientAccessIntent,
    ClientDeletion,
    Inbound,
    Operation,
)
from tunnelui.services.coordinator import SandboxEnvironment
from tunnelui.system.provider import FakeSystemProvider, SystemOperationError


def adopt(http):
    preview = http.post("/api/trusttunnel/adoption/preview").json()
    response = http.post(
        "/api/trusttunnel/adoption/confirm", json={"preview_id": preview["preview_id"]}
    )
    assert response.status_code == 201
    return response.json()["inbound_id"]


def create_attached(http, inbound_id, username="delete-me"):
    client = http.post("/api/clients", json={
        "username": username, "display_name": "Test client",
    }).json()
    response = http.post(f"/api/clients/{client['id']}/attachments", json={
        "inbound_id": inbound_id, "password": "synthetic-test-password",
        "idempotency_key": f"attach-{username}-key",
    })
    assert response.status_code == 201
    assert response.json()["operation"]["state"] == "succeeded"
    return client


def delete(http, client, key="delete-client-key"):
    return http.post(f"/api/clients/{client['id']}/delete", json={
        "revision": client["revision"], "idempotency_key": key,
    })


def test_create_delete_and_replay_preserves_other_credentials(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    original = sandbox_app.state.sandbox.environment.files["credentials"].read_bytes()
    assert b"delete-me" in original
    response = delete(sandbox_authenticated, client)
    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"
    assert delete(sandbox_authenticated, client).json() == response.json()
    credentials = sandbox_app.state.sandbox.environment.files["credentials"].read_bytes()
    assert b"delete-me" not in credentials and b"sandbox-alice" in credentials
    with sandbox_app.state.sessions() as db:
        assert db.get(Client, client["id"]) is None
        assert db.scalar(select(Attachment).where(Attachment.client_id == client["id"])) is None
        deletion = db.get(ClientDeletion, "delete-client-key")
        assert deletion.state == "succeeded"
        assert "synthetic-test-password" not in deletion.result_json


def test_drift_before_delete_and_pending_cancel_preview(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    runtime = sandbox_app.state.sandbox
    runtime.environment.files["rules"].write_bytes(b"# external change\n")
    failed = delete(sandbox_authenticated, client, "delete-drift-key")
    assert failed.status_code == 409
    assert failed.json()["code"] == "drift_conflict"
    with sandbox_app.state.sessions() as db:
        attachment = db.scalar(select(Attachment).where(Attachment.client_id == client["id"]))
        assert attachment.desired_state == "active"
    attachment_id = attachment.id
    response = sandbox_authenticated.put(f"/api/attachments/{attachment_id}", json={
        "enabled": False, "revision": attachment.revision,
        "idempotency_key": "disable-with-drift-key",
    })
    assert response.status_code == 409
    assert response.json()["code"] == "drift_conflict"
    assert sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/pending").json()[
        "changes"
    ] == []
    assert sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/reimport").status_code == 200

    class LateRulesEdit:
        def __init__(self, base):
            self.base = base

        def __getattr__(self, name):
            return getattr(self.base, name)

        def prepare_credentials(self, operation_id, content):
            self.base.prepare_credentials(operation_id, content)
            self.base.files["rules"].write_bytes(b"# later external change\n")

    runtime.coordinator.environments[runtime.environment.registry_id] = LateRulesEdit(
        runtime.environment
    )
    late = sandbox_authenticated.put(f"/api/attachments/{attachment_id}", json={
        "enabled": False, "revision": attachment.revision,
        "idempotency_key": "disable-after-preflight-key",
    })
    assert late.status_code == 200
    assert late.json()["operation"]["state"] == "failed"
    assert sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/reimport").json()[
        "code"
    ] == "pending_changes_require_cancel"
    preview = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/pending").json()
    assert preview["changed_files"] == ["rules"]
    assert preview["changes"][0]["username"] == "delete-me"
    assert preview["changes"][0]["desired_state"] == "disabled"
    assert sandbox_authenticated.post(
        f"/api/inbounds/{inbound_id}/cancel-pending",
        json={"fingerprint": "0" * 64},
    ).json()["code"] == "pending_preview_changed"
    cancelled = sandbox_authenticated.post(
        f"/api/inbounds/{inbound_id}/cancel-pending",
        json={"fingerprint": preview["fingerprint"]},
    )
    assert cancelled.status_code == 200 and cancelled.json()["cancelled"] == 1
    assert sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/reimport").status_code == 200


def test_delete_failure_keeps_client_and_retries_with_new_key(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    runtime = sandbox_app.state.sandbox
    runtime.provider.configure("health_failure_rollback_success")
    failed = delete(sandbox_authenticated, client, "delete-fail-key")
    assert failed.status_code == 200
    assert failed.json()["state"] == "partial"
    assert failed.json()["operations"][0]["state"] == "rolled_back"
    with sandbox_app.state.sessions() as db:
        assert db.get(Client, client["id"]) is not None
    assert b"delete-me" in runtime.environment.files["credentials"].read_bytes()
    assert delete(sandbox_authenticated, client, "delete-fail-key").json() == failed.json()
    runtime.provider.configure("healthy")
    retried = delete(sandbox_authenticated, client, "delete-retry-key")
    assert retried.json()["state"] == "succeeded"
    assert b"delete-me" not in runtime.environment.files["credentials"].read_bytes()


def test_last_active_client_cannot_be_deleted(sandbox_authenticated, sandbox_app):
    adopt(sandbox_authenticated)
    client = sandbox_authenticated.get("/api/clients").json()["items"][0]
    original = sandbox_app.state.sandbox.environment.files["credentials"].read_bytes()
    response = delete(sandbox_authenticated, client, "delete-last-key")
    assert response.status_code == 409
    assert response.json()["code"] == "empty_credentials_unsupported_v1_1_0"
    assert sandbox_app.state.sandbox.environment.files["credentials"].read_bytes() == original
    with sandbox_app.state.sessions() as db:
        assert db.get(Client, client["id"]) is not None


def test_multi_inbound_delete_keeps_client_until_all_verified(
    sandbox_authenticated, sandbox_app, tmp_path
):
    first_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, first_id)
    runtime = sandbox_app.state.sandbox
    second_root = tmp_path / "second"
    shutil.copytree(runtime.environment.files["credentials"].parent, second_root,
                    ignore=shutil.ignore_patterns("backups"))
    second_files = {
        name: second_root / f"{name}.toml"
        for name in ("vpn", "hosts", "credentials", "rules")
    }
    second = SandboxEnvironment(
        registry_id="second", files=second_files, backup_root=second_root / "backups",
        provider=FakeSystemProvider([True]), working_directory="/opt/second",
        exec_start=["/opt/second/trusttunnel_endpoint", "vpn.toml", "hosts.toml"],
        version="1.1.0", public_address="second.invalid:443",
    )
    runtime.coordinator.environments["second"] = second
    with sandbox_app.state.sessions() as db:
        inbound = Inbound(
            name="Second", kind="trusttunnel", registry_id="second",
            hashes_json=json.dumps({
                name: source_hash(path.read_bytes()) for name, path in second_files.items()
            }),
        )
        db.add(inbound)
        db.flush()
        for source in db.scalars(select(Attachment).where(
            Attachment.inbound_id == first_id
        )).all():
            db.add(Attachment(
                client_id=source.client_id, inbound_id=inbound.id,
                secret_ciphertext=source.secret_ciphertext,
                desired_state="active", applied_state="active", sync_state="active",
            ))
        db.commit()
    result = delete(sandbox_authenticated, client, "delete-multiple-key")
    assert result.status_code == 200 and result.json()["state"] == "succeeded"
    assert len(result.json()["operations"]) == 2
    assert b"delete-me" not in second_files["credentials"].read_bytes()
    assert b"sandbox-alice" in second_files["credentials"].read_bytes()
    with sandbox_app.state.sessions() as db:
        assert db.get(Client, client["id"]) is None


def test_delete_agent_failure_keeps_client_and_other_credentials(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    runtime = sandbox_app.state.sandbox
    original = runtime.environment.files["credentials"].read_bytes()

    class AgentUnavailable:
        def __init__(self, base):
            self.base = base

        def __getattr__(self, name):
            return getattr(self.base, name)

        def commit_credentials(self, operation_id, expected_hashes):
            raise SystemOperationError("agent_unavailable")

    runtime.coordinator.environments[runtime.environment.registry_id] = AgentUnavailable(
        runtime.environment
    )
    result = delete(sandbox_authenticated, client, "delete-agent-fail-key")
    assert result.status_code == 200
    assert result.json()["state"] == "partial"
    assert result.json()["operations"][0]["state"] == "rolled_back"
    assert runtime.environment.files["credentials"].read_bytes() == original
    with sandbox_app.state.sessions() as db:
        assert db.get(Client, client["id"]) is not None
        assert "synthetic-test-password" not in db.get(
            ClientDeletion, "delete-agent-fail-key"
        ).result_json


def test_delete_rechecks_drift_during_apply_and_serializes_inbound(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    runtime = sandbox_app.state.sandbox
    with runtime.locks.acquire(inbound_id):
        busy = delete(sandbox_authenticated, client, "delete-busy-key")
    assert busy.status_code == 409 and busy.json()["code"] == "operation_in_progress"
    original = runtime.environment.files["credentials"].read_bytes()

    class ExternalEditAfterPrepare:
        def __init__(self, base):
            self.base = base

        def __getattr__(self, name):
            return getattr(self.base, name)

        def prepare_credentials(self, operation_id, content):
            self.base.prepare_credentials(operation_id, content)
            self.base.files["rules"].write_bytes(b"# concurrent external edit\n")

    runtime.coordinator.environments[runtime.environment.registry_id] = (
        ExternalEditAfterPrepare(runtime.environment)
    )
    result = delete(sandbox_authenticated, client, "delete-late-drift-key")
    assert result.status_code == 200 and result.json()["state"] == "partial"
    assert result.json()["error_code"] == "drift_conflict"
    assert runtime.environment.files["credentials"].read_bytes() == original
    with sandbox_app.state.sessions() as db:
        assert db.get(Client, client["id"]) is not None


def test_connection_limits_are_applied_and_cancelled_safely(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    runtime = sandbox_app.state.sandbox
    with sandbox_app.state.sessions() as db:
        attachment = db.scalar(select(Attachment).where(Attachment.client_id == client["id"]))
        attachment_id, revision = attachment.id, attachment.revision
    updated = sandbox_authenticated.put(f"/api/attachments/{attachment_id}", json={
        "enabled": True, "revision": revision, "max_http2_conns": 7,
        "max_http3_conns": 9, "idempotency_key": "update-limits-key",
    })
    assert updated.status_code == 200
    assert updated.json()["operation"]["state"] == "succeeded"
    assert b"max_http2_conns = 7" in runtime.environment.files["credentials"].read_bytes()
    with sandbox_app.state.sessions() as db:
        attachment = db.get(Attachment, attachment_id)
        assert (attachment.applied_max_http2_conns, attachment.applied_max_http3_conns) == (7, 9)
        revision = attachment.revision
    runtime.environment.files["rules"].write_bytes(b"# external edit\n")
    failed = sandbox_authenticated.put(f"/api/attachments/{attachment_id}", json={
        "enabled": True, "revision": revision, "max_http2_conns": 11,
        "max_http3_conns": 13, "idempotency_key": "failed-limits-key",
    })
    assert failed.status_code == 409 and failed.json()["code"] == "drift_conflict"
    assert sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/pending").json()[
        "changes"
    ] == []
    assert sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/reimport").status_code == 200

    class LateRulesEdit:
        def __init__(self, base):
            self.base = base

        def __getattr__(self, name):
            return getattr(self.base, name)

        def prepare_credentials(self, operation_id, content):
            self.base.prepare_credentials(operation_id, content)
            self.base.files["rules"].write_bytes(b"# newer external edit\n")

    runtime.coordinator.environments[runtime.environment.registry_id] = LateRulesEdit(
        runtime.environment
    )
    late = sandbox_authenticated.put(f"/api/attachments/{attachment_id}", json={
        "enabled": True, "revision": revision, "max_http2_conns": 11,
        "max_http3_conns": 13, "idempotency_key": "late-limits-key",
    })
    assert late.status_code == 200
    assert late.json()["operation"]["state"] == "failed"
    preview = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/pending").json()
    assert preview["changes"][0]["desired_limits"] == [11, 13]
    runtime.environment.files["rules"].write_bytes(b"# changed again\n")
    stale = sandbox_authenticated.post(
        f"/api/inbounds/{inbound_id}/cancel-pending",
        json={"fingerprint": preview["fingerprint"]},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "pending_preview_changed"
    preview = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/pending").json()
    assert sandbox_authenticated.post(
        f"/api/inbounds/{inbound_id}/cancel-pending",
        json={"fingerprint": preview["fingerprint"]},
    ).status_code == 200
    with sandbox_app.state.sessions() as db:
        attachment = db.get(Attachment, attachment_id)
        assert (attachment.max_http2_conns, attachment.max_http3_conns) == (7, 9)


def test_reimport_accepts_known_disabled_attachment_without_remapping(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    with sandbox_app.state.sessions() as db:
        attachment = db.scalar(select(Attachment).where(Attachment.client_id == client["id"]))
        attachment_id, revision = attachment.id, attachment.revision
    result = sandbox_authenticated.put(f"/api/attachments/{attachment_id}", json={
        "enabled": False, "revision": revision,
        "idempotency_key": "disable-before-reimport-key",
    })
    assert result.json()["operation"]["state"] == "succeeded"
    sandbox_app.state.sandbox.environment.files["rules"].write_bytes(b"# external edit\n")
    imported = sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/reimport")
    assert imported.status_code == 200
    with sandbox_app.state.sessions() as db:
        attachment = db.get(Attachment, attachment_id)
        assert attachment.desired_state == "disabled"
        assert attachment.applied_state == "disabled"


def test_attachment_mutations_replay_without_duplicate_restart_or_secret(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = sandbox_authenticated.post("/api/clients", json={
        "username": "replay-user", "display_name": "Replay user",
    }).json()
    body = {
        "inbound_id": inbound_id, "password": "synthetic-replay-password",
        "idempotency_key": "replay-attachment-key",
    }
    first = sandbox_authenticated.post(
        f"/api/clients/{client['id']}/attachments", json=body
    )
    assert first.status_code == 201
    restarts = len(sandbox_app.state.sandbox.provider.actions)
    repeated = sandbox_authenticated.post(
        f"/api/clients/{client['id']}/attachments", json=body
    )
    assert repeated.status_code == 201 and repeated.json() == first.json()
    assert len(sandbox_app.state.sandbox.provider.actions) == restarts
    conflict = sandbox_authenticated.post(
        f"/api/clients/{client['id']}/attachments",
        json={**body, "password": "different-replay-password"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    attachment_id = first.json()["attachment_id"]
    update = {
        "enabled": False, "revision": 1, "idempotency_key": "replay-disable-key",
    }
    first_update = sandbox_authenticated.put(
        f"/api/attachments/{attachment_id}", json=update
    )
    assert first_update.status_code == 200
    repeated_update = sandbox_authenticated.put(
        f"/api/attachments/{attachment_id}", json=update
    )
    assert repeated_update.json() == first_update.json()
    with sandbox_app.state.sessions() as db:
        intent = db.get(AttachmentIntent, "replay-attachment-key")
        operation = db.scalar(select(Operation).where(
            Operation.idempotency_key == "replay-attachment-key"
        ))
        assert "synthetic-replay-password" not in str(intent.__dict__)
        assert "synthetic-replay-password" not in str(operation.__dict__)


def test_pending_create_cannot_be_cancelled_if_credential_is_present(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = sandbox_authenticated.post("/api/clients", json={
        "username": "uncertain-user", "display_name": "Uncertain user",
    }).json()
    runtime = sandbox_app.state.sandbox
    with sandbox_app.state.sessions() as db:
        db.add(Attachment(
            client_id=client["id"], inbound_id=inbound_id,
            secret_ciphertext=runtime.coordinator.box.encrypt("uncertain-password"),
            desired_state="active", applied_state="pending", sync_state="pending",
        ))
        db.commit()
    # Simulate a lost response after a real file replacement. The expected
    # hashes still describe the old file, so cancellation must fail closed.
    credentials = runtime.environment.files["credentials"]
    credentials.write_bytes(credentials.read_bytes() +
                            b'\n[[client]]\nusername = "uncertain-user"\n'
                            b'password = "uncertain-password"\n')
    preview = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/pending").json()
    response = sandbox_authenticated.post(
        f"/api/inbounds/{inbound_id}/cancel-pending",
        json={"fingerprint": preview["fingerprint"]},
    )
    assert response.status_code == 409 and response.json()["code"] == "pending_cancel_unsafe"
    with sandbox_app.state.sessions() as db:
        assert db.scalar(select(Attachment).where(Attachment.client_id == client["id"]))


def test_global_access_replay_is_idempotent_and_secret_free(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    body = {"enabled": False, "revision": client["revision"],
            "idempotency_key": "global-access-key"}
    first = sandbox_authenticated.put(f"/api/clients/{client['id']}/access", json=body)
    assert first.status_code == 200 and first.json()["state"] == "succeeded"
    actions = list(sandbox_app.state.sandbox.provider.actions)
    repeated = sandbox_authenticated.put(f"/api/clients/{client['id']}/access", json=body)
    assert repeated.status_code == 200 and repeated.json() == first.json()
    assert sandbox_app.state.sandbox.provider.actions == actions
    assert b"delete-me" not in sandbox_app.state.sandbox.environment.files[
        "credentials"
    ].read_bytes()
    conflict = sandbox_authenticated.put(f"/api/clients/{client['id']}/access", json={
        **body, "enabled": True,
    })
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    with sandbox_app.state.sessions() as db:
        intent = db.get(ClientAccessIntent, "global-access-key")
        assert intent.enabled is False
        assert "synthetic-test-password" not in str(intent.__dict__)


def test_global_access_partial_apply_can_be_finished_explicitly(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, inbound_id)
    runtime = sandbox_app.state.sandbox
    runtime.provider.configure("health_failure_rollback_success")
    body = {"enabled": False, "revision": client["revision"],
            "idempotency_key": "partial-access-key"}
    failed = sandbox_authenticated.put(f"/api/clients/{client['id']}/access", json=body)
    assert failed.status_code == 200 and failed.json()["state"] == "partial"
    assert failed.json()["operations"][0]["state"] == "rolled_back"
    assert sandbox_authenticated.put(f"/api/clients/{client['id']}/access", json=body
                                     ).json() == failed.json()
    pending = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/pending").json()
    assert pending["changes"][0]["desired_global_enabled"] is False
    assert pending["changes"][0]["applied_global_enabled"] is True
    runtime.provider.configure("healthy")
    applied = sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/apply-pending", json={
        "idempotency_key": "retry-partial-access-key",
    })
    assert applied.status_code == 200
    assert applied.json()["operation"]["state"] == "succeeded"
    assert b"delete-me" not in runtime.environment.files["credentials"].read_bytes()


def test_multi_inbound_global_cancel_previews_impact_and_recovers(
    sandbox_authenticated, sandbox_app, tmp_path
):
    first_id = adopt(sandbox_authenticated)
    client = create_attached(sandbox_authenticated, first_id)
    runtime = sandbox_app.state.sandbox
    second_root = tmp_path / "second-global"
    shutil.copytree(runtime.environment.files["credentials"].parent, second_root,
                    ignore=shutil.ignore_patterns("backups"))
    files = {name: second_root / f"{name}.toml"
             for name in ("vpn", "hosts", "credentials", "rules")}
    second = SandboxEnvironment(
        registry_id="second-global", files=files, backup_root=second_root / "backups",
        provider=FakeSystemProvider([True]), working_directory="/opt/second-global",
        exec_start=["/opt/second-global/trusttunnel_endpoint", "vpn.toml", "hosts.toml"],
        version="1.1.0", public_address="second.invalid:443",
    )
    runtime.coordinator.environments["second-global"] = second
    second_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    with sandbox_app.state.sessions() as db:
        db.add(Inbound(
            id=second_id, name="Second", kind="trusttunnel", registry_id="second-global",
            hashes_json=json.dumps({name: source_hash(path.read_bytes())
                                    for name, path in files.items()}),
        ))
        db.flush()
        for source in db.scalars(select(Attachment).where(
            Attachment.inbound_id == first_id
        )).all():
            db.add(Attachment(
                client_id=source.client_id, inbound_id=second_id,
                secret_ciphertext=source.secret_ciphertext,
                desired_state="active", applied_state="active", sync_state="active",
                applied_client_enabled=True,
            ))
        db.commit()

    class DriftSecondAfterFirstHealth:
        def __init__(self, base):
            self.base = base

        def __getattr__(self, name):
            return getattr(self.base, name)

        def health(self, inbound_id):
            healthy = self.base.health(inbound_id)
            files["rules"].write_bytes(b"# changed after first apply\n")
            return healthy

    class FirstEnvironment:
        def __init__(self, base):
            self.base = base
            self.provider = DriftSecondAfterFirstHealth(base.provider)

        def __getattr__(self, name):
            return getattr(self.base, name)

    runtime.coordinator.environments[runtime.environment.registry_id] = (
        FirstEnvironment(runtime.environment)
    )
    response = sandbox_authenticated.put(f"/api/clients/{client['id']}/access", json={
        "enabled": False, "revision": client["revision"],
        "idempotency_key": "multi-global-access-key",
    })
    assert response.status_code == 200 and response.json()["state"] == "partial"
    assert response.json()["error_code"] == "drift_conflict"
    assert response.json()["operations"][0]["state"] == "succeeded"
    preview = sandbox_authenticated.get(f"/api/inbounds/{second_id}/pending").json()
    assert {item["inbound_id"] for item in preview["affected_inbounds"]} == {
        first_id, second_id
    }
    cancelled = sandbox_authenticated.post(f"/api/inbounds/{second_id}/cancel-pending",
                                           json={"fingerprint": preview["fingerprint"]})
    assert cancelled.status_code == 200
    first_pending = sandbox_authenticated.get(f"/api/inbounds/{first_id}/pending").json()
    assert any(item["username"] == "delete-me" for item in first_pending["changes"])
    assert sandbox_authenticated.post(f"/api/inbounds/{second_id}/reimport").status_code == 200
    restored = sandbox_authenticated.post(f"/api/inbounds/{first_id}/apply-pending", json={
        "idempotency_key": "restore-first-global-key",
    })
    assert restored.status_code == 200
    assert restored.json()["operation"]["state"] == "succeeded"
    assert b"delete-me" in runtime.environment.files["credentials"].read_bytes()
