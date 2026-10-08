"""Linux adoption contract tests: fake agent, SQLite and no host operations."""

import json

import pytest
from sqlalchemy import select

from tunnelui.agent.client import AgentClientError
from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, Client, Inbound
from tunnelui.security import SecretBox, create_key
from tunnelui.services.linux_adoption import LinuxAdoptionService
from tunnelui.services.operations import InboundLocks
from tunnelui.system.linux import LinuxManagedEnvironment


class FakeAgent:
    def __init__(self):
        self.files = {
            "vpn": (
                b'listen_address = "127.0.0.1:8448"\n'
                b'credentials_file = "credentials.toml"\n'
                b'rules_file = "rules.toml"\n[listen_protocols.http2]\n'
            ),
            "hosts": b'[[main_hosts]]\nhostname = "vpn.example.invalid"\n',
            "credentials": b'[[client]]\nusername = "existing"\npassword = "private-fixture-password"\n',
            "rules": b'# existing rules\n',
        }
        self.metadata = {
            "service": "trusttunnel-staging.service",
            "working_directory": "/opt/trusttunnel-staging",
            "binary_path": "/opt/trusttunnel-staging/trusttunnel_endpoint",
            "vpn_config_path": "/opt/trusttunnel-staging/vpn.toml",
            "hosts_config_path": "/opt/trusttunnel-staging/hosts.toml",
            "credentials_path": "/opt/trusttunnel-staging/credentials.toml",
            "rules_path": "/opt/trusttunnel-staging/rules.toml",
            "public_address": "vpn.example.invalid:8448",
            "expected_version": "1.1.0",
        }
        self.backups = {}
        self.actions = []
        self.unavailable = False
        self.backup_unavailable = False
        self.fail_end_once = False

    def describe(self, _managed_id):
        if self.unavailable:
            raise AgentClientError("agent_unavailable")
        return self.metadata.copy()

    def snapshot(self, _managed_id):
        if self.unavailable:
            raise AgentClientError("agent_unavailable")
        return self.files.copy()

    def begin(self, _managed_id, operation_id):
        self.actions.append(("lock", operation_id))

    def end(self, _managed_id, operation_id):
        self.actions.append(("unlock", operation_id))
        if self.fail_end_once:
            self.fail_end_once = False
            raise AgentClientError("agent_unavailable")

    def backup(self, _managed_id, operation_id):
        if self.backup_unavailable:
            raise AgentClientError("agent_unavailable")
        self.backups[operation_id] = self.files.copy()
        self.actions.append(("backup", operation_id))

    def status(self, _managed_id):
        return {"running": True}


@pytest.fixture
def linux_adoption(app, tmp_path):
    agent = FakeAgent()
    environment = LinuxManagedEnvironment(
        "staging", agent, "1.1.0", "vpn.example.invalid:8448"
    )
    locks = InboundLocks()
    service = LinuxAdoptionService(agent, environment, locks)
    create_key(tmp_path / "linux-adoption.key")
    box = SecretBox(tmp_path / "linux-adoption.key")
    return agent, environment, service, box


def adopt(app, linux_adoption):
    _agent, _environment, service, box = linux_adoption
    token = service.detect()["preview_id"]
    with app.state.sessions() as db:
        return service.confirm(token, db, box, "admin"), token


def test_linux_adoption_discovery_import_and_idempotent_retry(app, linux_adoption):
    agent, _environment, service, box = linux_adoption
    original = agent.files.copy()
    assert service.discover()["listen_address"] == "127.0.0.1:8448"
    detected = service.detect()
    assert "private-fixture-password" not in json.dumps(detected)
    with app.state.sessions() as db:
        first = service.confirm(detected["preview_id"], db, box, "admin")
        second = service.confirm(detected["preview_id"], db, box, "admin")
        assert first == second
        assert db.scalar(select(Inbound)).registry_id == "staging"
        attachment = db.scalar(select(Attachment))
        assert box.decrypt(attachment.secret_ciphertext) == "private-fixture-password"
        assert db.scalar(select(Client)).username == "existing"
    assert agent.files == original
    assert agent.backups[first["backup_id"]] == original
    assert [action for action, _ in agent.actions].count("backup") == 1
    assert "restart" not in [action for action, _ in agent.actions]


def test_linux_adoption_rejects_drift_agent_failure_and_username_collision(
    app, linux_adoption
):
    agent, _environment, service, box = linux_adoption
    token = service.detect()["preview_id"]
    agent.files["vpn"] += b"# external edit\n"
    with app.state.sessions() as db, pytest.raises(DomainError, match="drift_conflict"):
        service.confirm(token, db, box, "admin")
    assert not agent.backups

    agent.unavailable = True
    with pytest.raises(DomainError, match="management_unavailable"):
        service.discover()
    agent.unavailable = False
    token = service.detect()["preview_id"]
    with app.state.sessions() as db:
        db.add(Client(username="existing", display_name="Unrelated"))
        db.commit()
        with pytest.raises(DomainError, match="adoption_username_conflict"):
            service.confirm(token, db, box, "admin")
        assert db.scalar(select(Inbound)) is None


def test_linux_adoption_rejects_metadata_and_version_mismatch(linux_adoption):
    agent, _environment, service, _box = linux_adoption
    agent.metadata["credentials_path"] = "/opt/elsewhere/credentials.toml"
    with pytest.raises(DomainError, match="managed_metadata_mismatch"):
        service.detect()
    agent.metadata["credentials_path"] = "/opt/trusttunnel-staging/credentials.toml"
    agent.metadata["expected_version"] = "1.2.0"
    with pytest.raises(DomainError, match="managed_metadata_mismatch"):
        service.detect()


def test_linux_adoption_backup_failure_does_not_commit(app, linux_adoption):
    agent, _environment, service, box = linux_adoption
    token = service.detect()["preview_id"]
    agent.backup_unavailable = True
    with app.state.sessions() as db, pytest.raises(DomainError, match="management_unavailable"):
        service.confirm(token, db, box, "admin")
    with app.state.sessions() as db:
        assert db.scalar(select(Inbound)) is None
        assert db.scalar(select(Client)) is None
    assert not agent.backups


def test_lost_release_reply_after_commit_can_be_retried(app, linux_adoption):
    agent, _environment, service, box = linux_adoption
    token = service.detect()["preview_id"]
    agent.fail_end_once = True
    with app.state.sessions() as db:
        with pytest.raises(DomainError, match="management_unavailable"):
            service.confirm(token, db, box, "admin")
        retried = service.confirm(token, db, box, "admin")
        assert retried["client_count"] == 1
        assert len(db.scalars(select(Inbound)).all()) == 1
        assert len(db.scalars(select(Client)).all()) == 1


def test_explicit_reimport_refreshes_bind_and_public_metadata_without_losing_clients(
    app, linux_adoption
):
    agent, environment, _service, box = linux_adoption
    adopted, _token = adopt(app, linux_adoption)
    inbound_id = adopted["inbound_id"]
    from tunnelui.services.coordinator import TrustTunnelCoordinator

    coordinator = TrustTunnelCoordinator(
        app.state.sessions, box, {"staging": environment}, InboundLocks()
    )
    agent.files["vpn"] = agent.files["vpn"].replace(
        b"127.0.0.1:8448", b"0.0.0.0:8448"
    )
    agent.metadata["public_address"] = "new.example.invalid:8448"
    environment.public_address = "new.example.invalid:8448"
    assert coordinator.check_drift(inbound_id)["drift"] is True
    assert coordinator.reimport(inbound_id, "admin") == {
        "config_state": "synced", "client_count": 1
    }
    with app.state.sessions() as db:
        inbound = db.get(Inbound, inbound_id)
        assert json.loads(inbound.metadata_json)["listen_address"] == "0.0.0.0:8448"
        assert inbound.public_address == "new.example.invalid:8448"
        assert db.scalar(select(Client)).username == "existing"
        assert db.scalar(select(Attachment)).applied_state == "active"
        ciphertext = db.scalar(select(Attachment)).secret_ciphertext
    assert coordinator.reimport(inbound_id, "admin")["config_state"] == "synced"
    with app.state.sessions() as db:
        assert db.scalar(select(Attachment)).secret_ciphertext == ciphertext
    assert coordinator.check_drift(inbound_id)["drift"] is False
    assert [action for action, _ in agent.actions].count("backup") == 1


def test_reimport_rejects_registry_mismatch_without_db_changes(app, linux_adoption):
    agent, environment, _service, box = linux_adoption
    inbound_id = adopt(app, linux_adoption)[0]["inbound_id"]
    from tunnelui.services.coordinator import TrustTunnelCoordinator

    coordinator = TrustTunnelCoordinator(
        app.state.sessions, box, {"staging": environment}, InboundLocks()
    )
    agent.metadata["public_address"] = "different.example.invalid:8448"
    with pytest.raises(DomainError, match="managed_metadata_mismatch"):
        coordinator.reimport(inbound_id, "admin")
    with app.state.sessions() as db:
        assert db.get(Inbound, inbound_id).public_address == "vpn.example.invalid:8448"
        assert db.scalar(select(Attachment)).applied_state == "active"


def test_reimport_preserves_pending_and_recovery_states(app, linux_adoption):
    agent, environment, _service, box = linux_adoption
    inbound_id = adopt(app, linux_adoption)[0]["inbound_id"]
    from tunnelui.services.coordinator import TrustTunnelCoordinator

    coordinator = TrustTunnelCoordinator(
        app.state.sessions, box, {"staging": environment}, InboundLocks()
    )
    with app.state.sessions() as db:
        attachment = db.scalar(select(Attachment))
        attachment.desired_state = "disabled"
        db.commit()
    with pytest.raises(DomainError, match="pending_changes_require_cancel"):
        coordinator.reimport(inbound_id, "admin")
    with app.state.sessions() as db:
        assert db.scalar(select(Attachment)).desired_state == "disabled"
        inbound = db.get(Inbound, inbound_id)
        inbound.config_state = "recovery_required"
        db.commit()
    agent.files["rules"] += b"# drift\n"
    assert coordinator.check_drift(inbound_id)["config_state"] == "recovery_required"
    with pytest.raises(DomainError, match="recovery_required"):
        coordinator.reimport(inbound_id, "admin")
