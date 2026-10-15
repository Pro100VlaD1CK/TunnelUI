import json
import shutil
import sqlite3
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from tunnelui.integrations.trusttunnel import parse_credentials, source_hash
from tunnelui.models import Attachment, Client, ExpiryAttempt, Inbound, Operation
from tunnelui.services.coordinator import SandboxEnvironment
from tunnelui.services.expiry import ExpiryScheduler
from tunnelui.system.provider import FakeSystemProvider, SystemOperationError


def adopt(http):
    preview = http.post("/api/trusttunnel/adoption/preview").json()
    response = http.post("/api/trusttunnel/adoption/confirm", json={
        "preview_id": preview["preview_id"]
    })
    assert response.status_code == 201
    return response.json()["inbound_id"]


def client(http, username="sandbox-alice"):
    return next(item for item in http.get("/api/clients").json()["items"]
                if item["username"] == username)


def change(http, item, username=None, expires_at=None, key="credential-change-key"):
    return http.put(f"/api/clients/{item['id']}/credentials", json={
        "username": username or item["username"], "display_name": item["display_name"],
        "comment": item["comment"], "expires_at": expires_at,
        "revision": item["revision"], "idempotency_key": key,
    })


def second_inbound(app, first_id, tmp_path):
    runtime = app.state.sandbox
    root = tmp_path / "second"
    shutil.copytree(runtime.environment.files["credentials"].parent, root,
                    ignore=shutil.ignore_patterns("backups"))
    files = {name: root / f"{name}.toml" for name in ("vpn", "hosts", "credentials", "rules")}
    second = SandboxEnvironment(
        registry_id="second", files=files, backup_root=root / "backups",
        provider=FakeSystemProvider([True]), working_directory="/opt/second",
        exec_start=["/opt/second/trusttunnel_endpoint", "vpn.toml", "hosts.toml"],
        version="1.1.0", public_address="second.invalid:443",
    )
    runtime.coordinator.environments["second"] = second
    with app.state.sessions() as db:
        inbound = Inbound(name="Second", kind="trusttunnel", registry_id="second",
                          hashes_json=json.dumps({
                              name: source_hash(path.read_bytes()) for name, path in files.items()
                          }))
        db.add(inbound)
        db.flush()
        for source in db.scalars(select(Attachment).where(
            Attachment.inbound_id == first_id
        )).all():
            attached_client = db.get(Client, source.client_id)
            db.add(Attachment(
                client_id=source.client_id, inbound_id=inbound.id,
                secret_ciphertext=source.secret_ciphertext,
                applied_secret_ciphertext=source.secret_ciphertext,
                applied_username=attached_client.username,
                applied_expires_at=attached_client.expires_at,
                applied_expiry_managed=attached_client.expiry_managed,
                applied_expired=False, applied_client_enabled=True,
                desired_state="active", applied_state="active", sync_state="active",
            ))
        db.commit()
        return inbound.id, second


def test_username_unattached_and_attached_profile(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    detached = sandbox_authenticated.post("/api/clients", json={
        "username": "detached", "display_name": "Detached",
    }).json()
    result = sandbox_authenticated.put(f"/api/clients/{detached['id']}", json={
        "username": "renamed-detached", "display_name": "Detached", "enabled": True,
        "comment": "", "expires_at": None, "revision": detached["revision"],
    })
    assert result.status_code == 200
    original = client(sandbox_authenticated)
    renamed = change(sandbox_authenticated, original, "new-alice")
    assert renamed.status_code == 200
    assert renamed.json()["state"] == "succeeded"
    assert change(sandbox_authenticated, original, "new-alice").json() == renamed.json()
    credentials = parse_credentials(
        sandbox_app.state.sandbox.environment.files["credentials"].read_bytes()
    )
    assert [item.username for item in credentials] == ["new-alice"]
    exported = sandbox_authenticated.post(f"/api/clients/{original['id']}/profiles/trusttunnel",
                                          json={"inbound_id": inbound_id,
                                                "format": "deeplink"})
    assert exported.status_code == 200
    assert "new-alice" in exported.json()["content"]
    assert "sandbox-alice" not in exported.json()["content"]


def test_new_client_with_expiry_is_explicitly_managed(sandbox_authenticated, sandbox_app):
    deadline = int(time.time()) + 3600
    created = sandbox_authenticated.post("/api/clients", json={
        "username": "time-limited", "display_name": "Time limited",
        "expires_at": deadline,
    })
    assert created.status_code == 201
    with sandbox_app.state.sessions() as db:
        row = db.get(Client, created.json()["id"])
        assert row.expires_at == deadline
        assert row.expiry_managed is True


def test_username_success_requires_applied_confirmation(
    sandbox_authenticated, sandbox_app, monkeypatch
):
    adopt(sandbox_authenticated)
    original = client(sandbox_authenticated)
    coordinator = sandbox_app.state.sandbox.coordinator
    monkeypatch.setattr(coordinator, "apply", lambda *args, **kwargs: {
        "id": "synthetic-unverified", "state": "succeeded", "error_code": None,
    })
    response = change(sandbox_authenticated, original, "unverified-alice",
                      key="unverified-rename-key")
    assert response.status_code == 200
    assert response.json()["state"] == "partial"
    assert response.json()["error_code"] == "apply_not_confirmed"
    listed = client(sandbox_authenticated, "unverified-alice")
    assert listed["attachments"][0]["applied_username"] == "sandbox-alice"


def test_multi_inbound_partial_and_profile_guard(
    sandbox_authenticated, sandbox_app, tmp_path
):
    first_id = adopt(sandbox_authenticated)
    second_id, second = second_inbound(sandbox_app, first_id, tmp_path)
    runtime = sandbox_app.state.sandbox
    failed_id = max(first_id, second_id)
    successful_id = min(first_id, second_id)
    failed_provider = second.provider if failed_id == second_id else runtime.provider
    failed_provider.configure("health_failure_rollback_success")
    original = client(sandbox_authenticated)
    result = change(sandbox_authenticated, original, "multi-alice", key="multi-rename-key")
    assert result.status_code == 200
    assert result.json()["state"] == "partial"
    assert [op["state"] for op in result.json()["operations"]] == [
        "succeeded", "rolled_back"
    ]
    listed = client(sandbox_authenticated, "multi-alice")
    applied = {item["inbound_id"]: item["applied_username"]
               for item in listed["attachments"]}
    assert applied[successful_id] == "multi-alice"
    assert applied[failed_id] == "sandbox-alice"
    failed_file = (second.files["credentials"] if failed_id == second_id
                   else runtime.environment.files["credentials"])
    assert b"sandbox-alice" in failed_file.read_bytes()
    blocked = sandbox_authenticated.post(
        f"/api/clients/{original['id']}/profiles/trusttunnel",
        json={"inbound_id": failed_id, "format": "toml"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "profile_pending_apply"
    failed_provider.configure("healthy")
    pending = sandbox_authenticated.post(f"/api/inbounds/{failed_id}/apply-pending", json={
        "idempotency_key": "resume-multi-rename-key"
    })
    assert pending.status_code == 200
    assert pending.json()["operation"]["state"] == "succeeded"


def test_rename_rejects_duplicate_and_drift(sandbox_authenticated, sandbox_app):
    adopt(sandbox_authenticated)
    other = sandbox_authenticated.post("/api/clients", json={
        "username": "taken", "display_name": "Taken",
    }).json()
    original = client(sandbox_authenticated)
    assert change(sandbox_authenticated, original, "taken").json()["code"] == "duplicate_username"
    runtime = sandbox_app.state.sandbox
    runtime.environment.files["rules"].write_bytes(b"# external change\n")
    response = change(sandbox_authenticated, original, "new-alice")
    assert response.status_code == 409
    assert response.json()["code"] == "drift_conflict"
    assert client(sandbox_authenticated)["username"] == "sandbox-alice"
    assert other["username"] == "taken"


def test_rename_agent_failure_preserves_old_file(sandbox_authenticated, sandbox_app):
    adopt(sandbox_authenticated)
    original = client(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    old_file = runtime.environment.files["credentials"].read_bytes()

    class Unavailable:
        def __init__(self, base):
            self.base = base

        def __getattr__(self, name):
            return getattr(self.base, name)

        def commit_credentials(self, operation_id, expected_hashes):
            raise SystemOperationError("agent_unavailable")

    runtime.coordinator.environments[runtime.environment.registry_id] = Unavailable(
        runtime.environment
    )
    response = change(sandbox_authenticated, original, "renamed-after-failure",
                      key="rename-agent-failure-key")
    assert response.status_code == 200
    assert response.json()["state"] == "partial"
    assert response.json()["operations"][0]["state"] == "rolled_back"
    assert runtime.environment.files["credentials"].read_bytes() == old_file
    listed = client(sandbox_authenticated, "renamed-after-failure")
    assert listed["attachments"][0]["applied_username"] == "sandbox-alice"
    preview = sandbox_authenticated.get(f"/api/inbounds/{listed['attachments'][0]['inbound_id']}/pending").json()
    cancelled = sandbox_authenticated.post(
        f"/api/inbounds/{listed['attachments'][0]['inbound_id']}/cancel-pending",
        json={"fingerprint": preview["fingerprint"]},
    )
    assert cancelled.status_code == 200
    assert client(sandbox_authenticated)["username"] == "sandbox-alice"


def test_rotation_rollback_retains_old_secret_and_redacts_lists(
    sandbox_authenticated, sandbox_app
):
    adopt(sandbox_authenticated)
    item = client(sandbox_authenticated)
    attachment = item["attachments"][0]
    runtime = sandbox_app.state.sandbox
    old_file = runtime.environment.files["credentials"].read_bytes()
    with sandbox_app.state.sessions() as db:
        old_ciphertext = db.get(Attachment, attachment["id"]).applied_secret_ciphertext
    runtime.provider.configure("health_failure_rollback_success")
    response = sandbox_authenticated.post(
        f"/api/attachments/{attachment['id']}/rotate-password", json={
            "password": "synthetic-new-secret-password", "revision": attachment["revision"],
            "idempotency_key": "rotate-password-key",
        }
    )
    assert response.status_code == 200
    assert response.json()["operation"]["state"] == "rolled_back"
    actions = list(runtime.provider.actions)
    replay = sandbox_authenticated.post(
        f"/api/attachments/{attachment['id']}/rotate-password", json={
            "password": "synthetic-new-secret-password", "revision": attachment["revision"],
            "idempotency_key": "rotate-password-key",
        }
    )
    assert replay.json() == response.json()
    assert runtime.provider.actions == actions
    assert runtime.environment.files["credentials"].read_bytes() == old_file
    with sandbox_app.state.sessions() as db:
        row = db.get(Attachment, attachment["id"])
        assert row.applied_secret_ciphertext == old_ciphertext
        assert row.secret_ciphertext != old_ciphertext
        operation = db.scalar(select(Operation).where(
            Operation.idempotency_key == "rotate-password-key"
        ))
        assert "synthetic-new-secret-password" not in json.dumps({
            column.name: getattr(operation, column.name)
            for column in Operation.__table__.columns
        })
    assert "synthetic-new-secret-password" not in sandbox_authenticated.get(
        "/api/clients"
    ).text
    runtime.provider.configure("healthy")
    resumed = sandbox_authenticated.post(f"/api/inbounds/{attachment['inbound_id']}/apply-pending", json={
        "idempotency_key": "rotate-resume-key"
    })
    assert resumed.json()["operation"]["state"] == "succeeded"
    assert "synthetic-new-secret-password" in runtime.environment.files[
        "credentials"
    ].read_text()


def test_rotation_failure_can_cancel_to_applied_secret(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    item = client(sandbox_authenticated)
    attachment = item["attachments"][0]
    runtime = sandbox_app.state.sandbox
    runtime.provider.configure("health_failure_rollback_success")
    failed = sandbox_authenticated.post(
        f"/api/attachments/{attachment['id']}/rotate-password", json={
            "password": "synthetic-cancel-secret", "revision": attachment["revision"],
            "idempotency_key": "rotate-cancel-key",
        }
    )
    assert failed.json()["operation"]["state"] == "rolled_back"
    preview = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/pending").json()
    assert preview["changes"][0]["credential_change"] is True
    cancelled = sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/cancel-pending", json={
        "fingerprint": preview["fingerprint"]
    })
    assert cancelled.status_code == 200
    with sandbox_app.state.sessions() as db:
        row = db.get(Attachment, attachment["id"])
        assert row.secret_ciphertext == row.applied_secret_ciphertext
    exported = sandbox_authenticated.post(
        f"/api/clients/{item['id']}/profiles/trusttunnel",
        json={"inbound_id": inbound_id, "format": "toml"},
    )
    assert exported.status_code == 200


def test_existing_expired_client_is_inert_until_opt_in(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    http = sandbox_authenticated
    extra = http.post("/api/clients", json={
        "username": "keeper", "display_name": "Keeper",
    }).json()
    attached = http.post(f"/api/clients/{extra['id']}/attachments", json={
        "inbound_id": inbound_id, "password": "synthetic-keeper-password",
        "idempotency_key": "attach-keeper-key",
    })
    assert attached.json()["operation"]["state"] == "succeeded"
    original = client(http)
    with sandbox_app.state.sessions() as db:
        row = db.get(Client, original["id"])
        row.expires_at = int(time.time()) - 10
        row.expiry_managed = False  # Simulates a pre-0004 expiry value.
        attached_row = db.scalar(select(Attachment).where(
            Attachment.client_id == original["id"]
        ))
        attached_row.applied_expires_at = row.expires_at
        attached_row.applied_expiry_managed = False
        db.commit()
    keeper_attachment = client(http, "keeper")["attachments"][0]
    unrelated_apply = http.put(f"/api/attachments/{keeper_attachment['id']}", json={
        "enabled": True, "revision": keeper_attachment["revision"],
        "max_http2_conns": 2, "idempotency_key": "legacy-unrelated-apply-key",
    })
    assert unrelated_apply.json()["operation"]["state"] == "succeeded"
    assert b"sandbox-alice" in sandbox_app.state.sandbox.environment.files[
        "credentials"
    ].read_bytes()
    scheduler = ExpiryScheduler(sandbox_app.state.sandbox.coordinator)
    assert scheduler.run_once()["enabled"] is False
    assert b"sandbox-alice" in sandbox_app.state.sandbox.environment.files[
        "credentials"
    ].read_bytes()
    view = http.get("/api/expiry/preview").json()
    assert any(item["username"] == "sandbox-alice" and item["overdue"]
               for item in view["affected"])
    assert http.put("/api/expiry/enabled", json={
        "enabled": True, "fingerprint": view["fingerprint"]
    }).status_code == 200
    assert scheduler.run_once()["succeeded"] == 1
    assert b"sandbox-alice" not in sandbox_app.state.sandbox.environment.files[
        "credentials"
    ].read_bytes()
    assert b"keeper" in sandbox_app.state.sandbox.environment.files[
        "credentials"
    ].read_bytes()
    assert ExpiryScheduler(sandbox_app.state.sandbox.coordinator).run_once()[
        "processed"
    ] == 0
    with sandbox_app.state.sessions() as db:
        attempt = db.scalar(select(ExpiryAttempt).where(
            ExpiryAttempt.client_id == original["id"]
        ))
        assert attempt.state == "succeeded"
        attempt.state = "running"  # Crash after confirmed apply, before attempt finalization.
        attempt.next_attempt_at = 0
        db.commit()
    actions_before = list(sandbox_app.state.sandbox.provider.actions)
    reconciled = ExpiryScheduler(sandbox_app.state.sandbox.coordinator).run_once()
    assert reconciled["succeeded"] == 1
    assert sandbox_app.state.sandbox.provider.actions == actions_before
    current = client(http)
    extended = change(http, current, expires_at=int(time.time()) + 3600,
                      key="extend-expiry-key")
    assert extended.status_code == 200
    assert extended.json()["state"] == "succeeded"
    assert b"sandbox-alice" in sandbox_app.state.sandbox.environment.files[
        "credentials"
    ].read_bytes()
    current = client(http)
    restored = change(http, current, expires_at=None, key="remove-expiry-key")
    assert restored.status_code == 200
    assert restored.json()["state"] == "succeeded"


def test_expiry_batch_reconciles_other_revoked_clients_without_second_restart(
    sandbox_authenticated, sandbox_app, monkeypatch
):
    inbound_id = adopt(sandbox_authenticated)
    http = sandbox_authenticated
    keeper = http.post("/api/clients", json={
        "username": "keeper", "display_name": "Keeper",
    }).json()
    assert http.post(f"/api/clients/{keeper['id']}/attachments", json={
        "inbound_id": inbound_id, "password": "synthetic-keeper-password",
        "idempotency_key": "attach-batch-keeper-key",
    }).json()["operation"]["state"] == "succeeded"
    second = http.post("/api/clients", json={
        "username": "second-expiring", "display_name": "Second",
    }).json()
    assert http.post(f"/api/clients/{second['id']}/attachments", json={
        "inbound_id": inbound_id, "password": "synthetic-second-password",
        "idempotency_key": "attach-batch-second-key",
    }).json()["operation"]["state"] == "succeeded"
    first = client(http)
    deadline = int(time.time()) + 3600
    assert change(http, first, expires_at=deadline,
                  key="batch-first-expiry-key").json()["state"] == "succeeded"
    assert change(http, client(http, "second-expiring"), expires_at=deadline,
                  key="batch-second-expiry-key").json()["state"] == "succeeded"
    monkeypatch.setattr(time, "time", lambda: deadline + 10)
    preview = http.get("/api/expiry/preview").json()
    assert http.put("/api/expiry/enabled", json={
        "enabled": True, "fingerprint": preview["fingerprint"],
    }).status_code == 200
    provider = sandbox_app.state.sandbox.provider
    before = provider.actions.count(("restart", inbound_id))
    result = ExpiryScheduler(sandbox_app.state.sandbox.coordinator).run_once()
    assert result["succeeded"] == 2
    assert provider.actions.count(("restart", inbound_id)) == before + 1
    with sandbox_app.state.sessions() as db:
        attempts = db.scalars(select(ExpiryAttempt)).all()
        assert len(attempts) == 2
        assert all(row.state == "succeeded" for row in attempts)
    assert ExpiryScheduler(sandbox_app.state.sandbox.coordinator).run_once()[
        "processed"
    ] == 0


def test_expiry_failure_backoff_and_manual_retry(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    http = sandbox_authenticated
    keeper = http.post("/api/clients", json={
        "username": "keeper", "display_name": "Keeper",
    }).json()
    assert http.post(f"/api/clients/{keeper['id']}/attachments", json={
        "inbound_id": inbound_id, "password": "synthetic-keeper-password",
        "idempotency_key": "attach-keeper-retry-key",
    }).json()["operation"]["state"] == "succeeded"
    original = client(http)
    with sandbox_app.state.sessions() as db:
        row = db.get(Client, original["id"])
        row.expires_at = int(time.time()) - 10
        row.expiry_managed = False
        db.commit()
    view = http.get("/api/expiry/preview").json()
    http.put("/api/expiry/enabled", json={
        "enabled": True, "fingerprint": view["fingerprint"]
    })
    runtime = sandbox_app.state.sandbox
    runtime.provider.configure("health_failure_rollback_success")
    scheduler = ExpiryScheduler(runtime.coordinator)
    first = scheduler.run_once()
    assert first["errors"] == 1
    restart_count = runtime.provider.actions.count(("restart", inbound_id))
    assert restart_count >= 2  # Apply and rollback.
    assert scheduler.run_once()["processed"] == 0
    assert runtime.provider.actions.count(("restart", inbound_id)) == restart_count
    with sandbox_app.state.sessions() as db:
        attempt = db.scalar(select(ExpiryAttempt))
        assert attempt.state == "error" and attempt.next_attempt_at is not None
        attempt_id = attempt.id
    runtime.provider.configure("healthy")
    assert http.post(f"/api/expiry/attempts/{attempt_id}/retry").status_code == 200
    assert ExpiryScheduler(runtime.coordinator).run_once()["succeeded"] == 1


def test_migration_0004_keeps_legacy_expiry_opted_out(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "upgrade.db"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "0003")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO clients (id, username, display_name, enabled, comment, "
            "expires_at, created_at, updated_at, revision) VALUES "
            "('client-1', 'legacy', 'Legacy', 1, '', 1, 1, 1, 1)"
        )
        connection.execute(
            "INSERT INTO inbounds (id, name, kind, enabled, metadata_json, "
            "hashes_json, config_state, created_at, updated_at) VALUES "
            "('inbound-1', 'Legacy', 'trusttunnel', 1, '{}', '{}', 'synced', 1, 1)"
        )
        connection.execute(
            "INSERT INTO attachments (id, client_id, inbound_id, secret_ciphertext, "
            "sync_state, enabled, desired_state, applied_state, created_at, updated_at, "
            "revision) VALUES ('attachment-1', 'client-1', 'inbound-1', 'encrypted-fixture', "
            "'active', 1, 'active', 'active', 1, 1, 1)"
        )
    command.upgrade(config, "0004")
    command.upgrade(config, "0004")  # Re-running the upgrade must preserve existing rows.
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT expiry_managed FROM clients").fetchone() == (0,)
        applied = connection.execute(
            "SELECT applied_username, applied_secret_ciphertext, applied_expiry_managed, "
            "applied_expired FROM attachments"
        ).fetchone()
        assert applied == ("legacy", "encrypted-fixture", 0, 0)
        assert connection.execute("SELECT enabled FROM expiry_settings").fetchone() == (0,)


def test_expiry_last_active_blocks_without_restart(sandbox_authenticated, sandbox_app):
    adopt(sandbox_authenticated)
    original = client(sandbox_authenticated)
    with sandbox_app.state.sessions() as db:
        row = db.get(Client, original["id"])
        row.expires_at = int(time.time()) - 10
        row.expiry_managed = False
        db.commit()
    view = sandbox_authenticated.get("/api/expiry/preview").json()
    sandbox_authenticated.put("/api/expiry/enabled", json={
        "enabled": True, "fingerprint": view["fingerprint"]
    })
    runtime = sandbox_app.state.sandbox
    old_file = runtime.environment.files["credentials"].read_bytes()
    result = ExpiryScheduler(runtime.coordinator).run_once()
    assert result["errors"] == 1
    assert runtime.environment.files["credentials"].read_bytes() == old_file
    with sandbox_app.state.sessions() as db:
        attempt = db.scalar(select(ExpiryAttempt))
        assert attempt.state == "blocked"
        assert attempt.error_code == "empty_credentials_unsupported_v1_1_0"
