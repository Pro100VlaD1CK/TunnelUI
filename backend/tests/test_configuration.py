import json
import shutil
import uuid

import httpx
import pytest
from sqlalchemy import select

from tunnelui.agent.protocol import ProtocolError, decode_request
from tunnelui.integrations.trusttunnel import source_hash
from tunnelui.models import AuditEvent, Inbound, Operation
from tunnelui.services.configuration import ConfigurationService
from tunnelui.services.coordinator import SandboxEnvironment, SimulatedCrash
from tunnelui.services.runtime_metrics import RuntimeMetricsService
from tunnelui.system.provider import FakeSystemProvider, SystemOperationError


def adopt(http):
    preview = http.post("/api/trusttunnel/adoption/preview").json()
    response = http.post("/api/trusttunnel/adoption/confirm", json={
        "preview_id": preview["preview_id"],
    })
    assert response.status_code == 201
    return response.json()["inbound_id"]


def preview_settings(http, inbound_id, values):
    return http.post(f"/api/inbounds/{inbound_id}/settings/preview", json={
        "values": values,
    })


def apply_settings(http, inbound_id, values, fingerprint, key="settings-apply-key"):
    return http.post(f"/api/inbounds/{inbound_id}/settings/apply", json={
        "values": values, "fingerprint": fingerprint, "idempotency_key": key,
    })


def test_settings_preview_apply_preserves_unknown_and_confirms_health(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    path = runtime.environment.files["vpn"]
    original = path.read_bytes()
    view = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/settings")
    assert view.status_code == 200
    assert view.json()["values"]["ipv6_available"] is True
    assert view.json()["listen_address"] == "0.0.0.0:443"
    preview = preview_settings(sandbox_authenticated, inbound_id, {
        "ipv6_available": False,
    })
    assert preview.status_code == 200
    assert preview.json()["changed_files"] == ["vpn"]
    assert path.read_bytes() == original
    applied = apply_settings(sandbox_authenticated, inbound_id,
                             {"ipv6_available": False}, preview.json()["fingerprint"])
    assert applied.status_code == 200
    assert applied.json()["operation"]["state"] == "succeeded"
    assert b"ipv6_available = false" in path.read_bytes()
    assert b"credentials_file" in path.read_bytes()
    assert runtime.provider.actions.count(("restart", inbound_id)) == 1
    assert apply_settings(sandbox_authenticated, inbound_id, {"ipv6_available": False},
                          preview.json()["fingerprint"]).json() == applied.json()
    with sandbox_app.state.sessions() as db:
        operation = db.scalar(select(Operation).where(
            Operation.idempotency_key == "settings-apply-key"
        ))
        assert operation.kind == "settings_apply"
        assert "credentials_file" not in operation.request_fingerprint


def test_config_content_never_enters_operation_or_audit(sandbox_authenticated, sandbox_app):
    vpn = sandbox_app.state.sandbox.environment.files["vpn"]
    vpn.write_bytes(vpn.read_bytes() + b"\n# private-token-sentinel\n")
    inbound_id = adopt(sandbox_authenticated)
    preview = preview_settings(sandbox_authenticated, inbound_id,
                               {"ipv6_available": False}).json()
    response = apply_settings(sandbox_authenticated, inbound_id,
                              {"ipv6_available": False}, preview["fingerprint"],
                              key="settings-secret-test-key")
    assert response.json()["operation"]["state"] == "succeeded"
    assert b"private-token-sentinel" in vpn.read_bytes()
    with sandbox_app.state.sessions() as db:
        operation = db.scalar(select(Operation).where(
            Operation.idempotency_key == "settings-secret-test-key"
        ))
        audit = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == inbound_id)).all()
        stored = repr({column.name: getattr(operation, column.name)
                       for column in Operation.__table__.columns}) + repr([
                           {column.name: getattr(row, column.name)
                            for column in AuditEvent.__table__.columns} for row in audit
                       ])
        assert "private-token-sentinel" not in stored


def test_settings_rejects_invalid_unknown_drift_and_changed_preview(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    http = sandbox_authenticated
    assert preview_settings(http, inbound_id, {"listen_address": "0.0.0.0:1"}).status_code == 422
    assert preview_settings(http, inbound_id, {
        "client_listener_timeout_secs": -1,
    }).status_code == 409
    view = preview_settings(http, inbound_id, {"ipv6_available": False}).json()
    bad = apply_settings(http, inbound_id, {"ipv6_available": False}, "0" * 64,
                         key="settings-wrong-preview-key")
    assert bad.status_code == 409
    assert bad.json()["code"] == "config_preview_changed"
    sandbox_app.state.sandbox.environment.files["hosts"].write_bytes(b"# external\n")
    blocked = apply_settings(http, inbound_id, {"ipv6_available": False},
                             view["fingerprint"], key="settings-drift-key")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "drift_conflict"


def test_settings_rejects_unverified_version(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    with sandbox_app.state.sessions() as db:
        inbound = db.get(Inbound, inbound_id)
        metadata = json.loads(inbound.metadata_json)
        metadata["version"] = "2.0.0"
        inbound.metadata_json = json.dumps(metadata)
        db.commit()
    response = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/settings")
    assert response.status_code == 409
    assert response.json()["code"] == "unsupported_endpoint_version"


def test_settings_rejects_registry_version_mismatch(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    object.__setattr__(sandbox_app.state.sandbox.environment, "version", "2.0.0")
    response = sandbox_authenticated.get(f"/api/inbounds/{inbound_id}/settings")
    assert response.status_code == 409
    assert response.json()["code"] == "unsupported_endpoint_version"


def test_external_edit_between_preview_and_commit_is_not_overwritten(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    proposal = {"ipv6_available": False}
    preview = preview_settings(sandbox_authenticated, inbound_id, proposal).json()
    vpn = runtime.environment.files["vpn"]
    original = vpn.read_bytes()

    class ExternalEdit:
        def __init__(self, base):
            self.base = base

        def __getattr__(self, name):
            return getattr(self.base, name)

        def commit_config(self, operation_id, file_id, expected_hashes):
            vpn.write_bytes(original + b"\n# concurrent external edit\n")
            return self.base.commit_config(operation_id, file_id, expected_hashes)

    runtime.coordinator.environments[runtime.environment.registry_id] = ExternalEdit(
        runtime.environment
    )
    result = apply_settings(sandbox_authenticated, inbound_id, proposal,
                            preview["fingerprint"], key="settings-external-edit-key")
    assert result.status_code == 200
    assert result.json()["operation"]["state"] == "failed"
    assert vpn.read_bytes() == original + b"\n# concurrent external edit\n"
    with sandbox_app.state.sessions() as db:
        operation = db.scalar(select(Operation).where(
            Operation.idempotency_key == "settings-external-edit-key"
        ))
        assert operation.error_code == "drift_conflict"
        assert "# concurrent external edit" not in operation.request_fingerprint


def test_rules_order_preview_apply_and_validation(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    http = sandbox_authenticated
    rules = [
        {"cidr": "192.0.2.0/24", "action": "deny"},
        {"client_random_prefix": "aabb/ffff", "action": "allow"},
    ]
    view = http.post(f"/api/inbounds/{inbound_id}/rules/preview", json={
        "rules": rules,
    })
    assert view.status_code == 200
    result = http.post(f"/api/inbounds/{inbound_id}/rules/apply", json={
        "rules": rules, "fingerprint": view.json()["fingerprint"],
        "idempotency_key": "rules-apply-key",
    })
    assert result.status_code == 200
    assert result.json()["operation"]["state"] == "succeeded"
    file = sandbox_app.state.sandbox.environment.files["rules"].read_text()
    assert file.index("192.0.2.0/24") < file.index("aabb/ffff")
    listed = http.get(f"/api/inbounds/{inbound_id}/rules").json()
    assert [item["action"] for item in listed["rules"]] == ["deny", "allow"]
    for invalid in (
        {"cidr": "bad", "action": "deny"},
        {"client_random_prefix": "zz", "action": "allow"},
        {"client_random_prefix": "aa/ffff", "action": "deny"},
        {"action": "maybe"},
        {"action": "allow", "username": "alice"},
    ):
        assert http.post(f"/api/inbounds/{inbound_id}/rules/preview", json={
            "rules": [invalid],
        }).status_code in {409, 422}


def test_config_rollback_and_recovery_use_correct_file(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    http = sandbox_authenticated
    path = runtime.environment.files["vpn"]
    original = path.read_bytes()
    view = preview_settings(http, inbound_id, {"ipv6_available": False}).json()
    runtime.provider.configure("health_failure_rollback_success")
    result = apply_settings(http, inbound_id, {"ipv6_available": False},
                            view["fingerprint"], key="settings-rollback-key")
    assert result.json()["operation"]["state"] == "rolled_back"
    assert path.read_bytes() == original
    runtime.provider.configure("healthy")
    coordinator = runtime.coordinator
    service = ConfigurationService(coordinator, "test-admin")
    view, content = service.preview_settings(inbound_id, {"ipv6_available": False})
    coordinator.interrupt_at = "after_replace"
    with pytest.raises(SimulatedCrash):
        coordinator.apply(inbound_id, "settings-crash-key", "test-admin",
                          "settings_apply", replacement=("vpn", content),
                          request_fingerprint="1" * 64)
    assert path.read_bytes() != original
    with sandbox_app.state.sessions() as db:
        operation = db.scalar(select(Operation).where(
            Operation.idempotency_key == "settings-crash-key"
        ))
        recovered = coordinator.recover(operation.id)
    assert recovered["state"] == "rolled_back"
    assert path.read_bytes() == original


def test_config_agent_failure_and_needs_recovery(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    path = runtime.environment.files["rules"]
    original = path.read_bytes()
    proposed = [{"action": "deny"}]
    view = sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/rules/preview",
                                      json={"rules": proposed}).json()

    class Unavailable:
        def __init__(self, base):
            self.base = base

        def __getattr__(self, name):
            return getattr(self.base, name)

        def commit_config(self, operation_id, file_id, expected_hashes):
            raise SystemOperationError("agent_unavailable")

    runtime.coordinator.environments[runtime.environment.registry_id] = Unavailable(
        runtime.environment
    )
    failed = sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/rules/apply", json={
        "rules": proposed, "fingerprint": view["fingerprint"],
        "idempotency_key": "rules-agent-failure-key",
    })
    assert failed.json()["operation"]["state"] == "rolled_back"
    assert path.read_bytes() == original
    runtime.coordinator.environments[runtime.environment.registry_id] = runtime.environment
    runtime.provider.configure("rollback_failure")
    blocked = sandbox_authenticated.post(f"/api/inbounds/{inbound_id}/rules/apply", json={
        "rules": proposed, "fingerprint": view["fingerprint"],
        "idempotency_key": "rules-recovery-key",
    })
    assert blocked.json()["operation"]["state"] == "needs_recovery"


def test_config_does_not_change_other_inbound(sandbox_authenticated, sandbox_app, tmp_path):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    root = tmp_path / "second"
    shutil.copytree(runtime.environment.files["vpn"].parent, root,
                    ignore=shutil.ignore_patterns("backups"))
    files = {name: root / f"{name}.toml" for name in ("vpn", "hosts", "credentials", "rules")}
    second = SandboxEnvironment(
        registry_id="second", files=files, backup_root=root / "backups",
        provider=FakeSystemProvider([True]), working_directory="/opt/second",
        exec_start=["/opt/second/trusttunnel_endpoint", "vpn.toml", "hosts.toml"],
        version="1.1.0", public_address="second.invalid:443",
    )
    runtime.coordinator.environments["second"] = second
    with sandbox_app.state.sessions() as db:
        source = db.get(Inbound, inbound_id)
        db.add(Inbound(name="Second", kind="trusttunnel", registry_id="second",
                       metadata_json=source.metadata_json,
                       hashes_json=json.dumps({
                           name: source_hash(path.read_bytes()) for name, path in files.items()
                       })))
        db.commit()
    original = files["vpn"].read_bytes()
    view = preview_settings(sandbox_authenticated, inbound_id,
                            {"ipv6_available": False}).json()
    applied = apply_settings(sandbox_authenticated, inbound_id,
                             {"ipv6_available": False}, view["fingerprint"],
                             key="settings-one-inbound-key")
    assert applied.json()["operation"]["state"] == "succeeded"
    assert files["vpn"].read_bytes() == original


def test_metrics_read_only_and_safe_loopback(sandbox_authenticated, sandbox_app):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    with sandbox_app.state.sessions() as db:
        service = RuntimeMetricsService(runtime.coordinator)
        assert service.clients(db, inbound_id)["state"] == "not_configured"
    vpn = runtime.environment.files["vpn"]
    original = vpn.read_text()
    vpn.write_text(original + "\n[metrics]\naddress = \"127.0.0.1:1987\"\n"
                   "per_client_metrics = true\n")
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, json=[{
            "username": "sandbox-alice", "ip": None, "sessions": 0,
            "inbound": 0, "outbound": 0,
        }])

    with sandbox_app.state.sessions() as db:
        service = RuntimeMetricsService(runtime.coordinator,
                                        httpx.MockTransport(respond))
        result = service.clients(db, inbound_id)
        assert result["state"] == "available"
        assert result["clients"][0]["sessions"] == 0
        assert result["counter_scope"] == "endpoint_process"
    assert requests == ["http://127.0.0.1:1987/clients"]
    vpn.write_text(vpn.read_text().replace("127.0.0.1", "0.0.0.0"))
    with sandbox_app.state.sessions() as db:
        result = RuntimeMetricsService(runtime.coordinator,
                                       httpx.MockTransport(respond)).clients(db, inbound_id)
        assert result["state"] == "unsafe_listener"
    assert len(requests) == 1


def test_metrics_endpoint_failure_has_explicit_unavailable_state(
    sandbox_authenticated, sandbox_app
):
    inbound_id = adopt(sandbox_authenticated)
    runtime = sandbox_app.state.sandbox
    vpn = runtime.environment.files["vpn"]
    vpn.write_bytes(vpn.read_bytes() + b'\n[metrics]\naddress = "127.0.0.1:1987"\n'
                    b'per_client_metrics = true\n')
    with sandbox_app.state.sessions() as db:
        service = RuntimeMetricsService(runtime.coordinator,
                                        httpx.MockTransport(lambda _: httpx.Response(503)))
        assert service.clients(db, inbound_id) == {"state": "unavailable", "clients": []}


def test_agent_config_protocol_rejects_paths_and_credentials_target():
    base = {"version": 1, "request_id": str(uuid.uuid4()),
            "managed_id": "primary", "operation": "files.prepare_config"}
    args = {"operation_id": str(uuid.uuid4()), "file_id": "vpn",
            "content_b64": "YQ=="}
    decoded, parsed = decode_request(json.dumps(base | {"arguments": args}).encode())
    assert decoded.operation == "files.prepare_config"
    assert parsed.file_id == "vpn"
    for bad in (args | {"file_id": "credentials"},
                args | {"file_id": "/etc/shadow"},
                args | {"path": "/etc/shadow"}):
        with pytest.raises(ProtocolError, match="malformed_request"):
            decode_request(json.dumps(base | {"arguments": bad}).encode())
