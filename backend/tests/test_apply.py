import os

import pytest

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.trusttunnel import source_hash
from tunnelui.services.apply import SandboxApplyService
from tunnelui.system.files import atomic_replace
from tunnelui.system.provider import FakeSystemProvider

OLD = b'[[client]]\nusername = "a"\npassword = "old-secret"\n'
NEW = b'[[client]]\nusername = "a"\npassword = "new-secret"\n'


def setup(tmp_path, health):
    files = {}
    for key, content in {"credentials": OLD, "vpn": b"# vpn", "hosts": b"# hosts", "rules": b"# rules"}.items():
        path = tmp_path / f"{key}.toml"
        path.write_bytes(content)
        files[key] = path
    provider = FakeSystemProvider(health)
    service = SandboxApplyService(files, tmp_path / "backups", provider)
    hashes = {key: source_hash(value) for key, value in service.snapshot().items()}
    return service, provider, hashes


def test_apply_success_and_backup(tmp_path):
    service, provider, hashes = setup(tmp_path, [True])
    result = service.apply_credentials("fixture", hashes, NEW)
    assert result.state == "applied"
    assert service.files["credentials"].read_bytes() == NEW
    assert (tmp_path / "backups" / result.operation_id / "credentials").read_bytes() == OLD
    assert provider.actions == [("restart", "fixture"), ("health", "fixture")]
    assert "secret" not in str(service.events)


@pytest.mark.parametrize(("health", "expected"), [([False, True], "rolled_back"), ([False, False], "rollback_failed")])
def test_health_failure_rollback(tmp_path, health, expected):
    service, provider, hashes = setup(tmp_path, health)
    assert service.apply_credentials("fixture", hashes, NEW).state == expected
    assert service.files["credentials"].read_bytes() == OLD
    assert len(provider.actions) == 4


def test_drift_any_source_blocks_restart(tmp_path):
    service, provider, hashes = setup(tmp_path, [True])
    service.files["rules"].write_bytes(b"# external edit")
    with pytest.raises(DomainError, match="drift_conflict"):
        service.apply_credentials("fixture", hashes, NEW)
    assert not provider.actions
    assert service.files["credentials"].read_bytes() == OLD


def test_atomic_replace_failure_keeps_original(tmp_path, monkeypatch):
    target = tmp_path / "credentials.toml"
    target.write_bytes(OLD)
    def fail(*_):
        raise OSError("injected rename failure")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        atomic_replace(target, NEW)
    assert target.read_bytes() == OLD
    assert list(tmp_path.iterdir()) == [target]


def test_restart_failure_is_not_success(tmp_path):
    service, provider, hashes = setup(tmp_path, [True])
    provider.fail_restart = True
    assert service.apply_credentials("fixture", hashes, NEW).state == "rollback_failed"


def test_invalid_credentials_never_written(tmp_path):
    service, provider, hashes = setup(tmp_path, [True])
    with pytest.raises(DomainError):
        service.apply_credentials("fixture", hashes, b"client = []")
    assert not provider.actions


def test_real_provider_not_accepted(tmp_path):
    with pytest.raises(ValueError):
        SandboxApplyService({}, tmp_path, object())
