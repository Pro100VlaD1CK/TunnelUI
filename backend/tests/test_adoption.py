import pytest
from sqlalchemy import select

from tunnelui.domain.errors import DomainError
from tunnelui.models import Attachment, Client, Inbound
from tunnelui.security import SecretBox, create_key
from tunnelui.services.adoption import SandboxAdoptionService
from tunnelui.services.apply import SandboxApplyService
from tunnelui.system.provider import FakeSystemProvider


def fixture_service(tmp_path):
    content = {
        "vpn": b'credentials_file = "credentials.toml"\nrules_file = "rules.toml"\n[listen_protocols.http2]\n',
        "hosts": b'[[main_hosts]]\nhostname = "example.test"\n',
        "credentials": b'[[client]]\nusername = "existing"\npassword = "imported-fixture-secret"\nmax_http3_conns = 2\n',
        "rules": b'# preserve this\n[[rule]]\naction="allow"\n',
    }
    paths = {}
    for key, value in content.items():
        paths[key] = tmp_path / f"{key}.toml"
        paths[key].write_bytes(value)
    provider = FakeSystemProvider()
    sandbox = SandboxApplyService(paths, tmp_path / "backups", provider)
    service = SandboxAdoptionService(sandbox, "fixture-inbound", "/opt/trusttunnel",
                                     ["/opt/trusttunnel/endpoint", "vpn.toml", "hosts.toml"], "1.1.0")
    key_path = tmp_path / "master.key"
    create_key(key_path)
    return service, sandbox, content, SecretBox(key_path)


def test_adoption_backup_encrypted_import_no_mutation(tmp_path, app):
    service, sandbox, original, box = fixture_service(tmp_path)
    detected = service.detect()
    assert "imported-fixture-secret" not in str(detected)
    with app.state.sessions() as db:
        result = service.confirm(detected["preview_id"], db, box, "admin")
        attachment = db.scalar(select(Attachment))
        assert box.decrypt(attachment.secret_ciphertext) == "imported-fixture-secret"
        assert attachment.max_http3_conns == 2
        assert db.scalar(select(Client)).username == "existing"
        assert "secret" not in db.scalar(select(Inbound)).metadata_json
        with pytest.raises(DomainError, match="adoption_preview_expired"):
            service.confirm(detected["preview_id"], db, box, "admin")
    assert sandbox.snapshot() == original
    assert not sandbox.provider.actions
    assert (sandbox.backup_root / result["backup_id"] / "credentials").read_bytes() == original["credentials"]


def test_adoption_drift_rejected(tmp_path, app):
    service, sandbox, _, box = fixture_service(tmp_path)
    detected = service.detect()
    sandbox.files["rules"].write_bytes(b"# changed externally")
    with app.state.sessions() as db, pytest.raises(DomainError, match="drift_conflict"):
        service.confirm(detected["preview_id"], db, box, "admin")


def test_adoption_existing_username_requires_mapping(tmp_path, app):
    service, _, _, box = fixture_service(tmp_path)
    detected = service.detect()
    with app.state.sessions() as db:
        db.add(Client(username="existing", display_name="Do not overwrite"))
        db.commit()
        with pytest.raises(DomainError, match="adoption_username_conflict"):
            service.confirm(detected["preview_id"], db, box, "admin")
        assert not db.scalar(select(Inbound))
