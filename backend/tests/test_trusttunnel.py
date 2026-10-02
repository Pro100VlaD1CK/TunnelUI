import pytest

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.trusttunnel import (
    Credential,
    capabilities,
    export_command,
    parse_credentials,
    parse_document,
    preview,
    render_credentials,
    resolve_config_path,
)

VPN = b'''# preserved comment
listen_address = "0.0.0.0:443"
credentials_file = "credentials.toml"
rules_file = "rules.toml"
unknown = "preserve"
[listen_protocols.http1]
[listen_protocols.http2]
[listen_protocols.quic]
[forward_protocol]
direct = {}
'''
HOSTS = b'[[main_hosts]]\nhostname = "vpn.example.test"\n'
CREDENTIALS = b'[[client]]\nusername = "ivan"\npassword = "fixture-secret"\n'


def test_current_parse_relative_paths_and_safe_preview():
    result = preview("/opt/trusttunnel", ["/opt/trusttunnel/trusttunnel_endpoint", "vpn.toml", "hosts.toml"],
                     {"vpn": VPN, "hosts": HOSTS, "credentials": CREDENTIALS}, "v1.1.0")
    assert result["credentials_path"] == "/opt/trusttunnel/credentials.toml"
    assert result["protocols"] == ["http1", "http2", "quic"]
    assert result["client_count"] == 1
    assert "fixture-secret" not in str(result)
    doc = parse_document(VPN)
    doc["listen_address"] = "127.0.0.1:443"
    assert '# preserved comment' in doc.as_string()
    assert 'unknown = "preserve"' in doc.as_string()


@pytest.mark.parametrize(("path", "expected"), [("vpn.toml", "/opt/tt/vpn.toml"), ("../rules.toml", "/opt/rules.toml"), ("/etc/tt/rules.toml", "/etc/tt/rules.toml")])
def test_path_resolution(path, expected):
    assert resolve_config_path("/opt/tt", path) == expected


def test_render_enabled_expiry_and_limits():
    values = [Credential("a", "secret", max_http2_conns=0), Credential("b", "secret", enabled=False),
              Credential("c", "secret", expires_at=100), Credential("d", "secret", expires_at=101)]
    parsed = parse_credentials(render_credentials(values, 100))
    assert [p.username for p in parsed] == ["a", "d"]
    assert parsed[0].max_http2_conns == 0
    assert "secret" not in repr(values)


@pytest.mark.parametrize("values", [[], [Credential("a", "s", enabled=False)], [Credential("a", "s", expires_at=0)]])
def test_empty_credentials_refused(values):
    with pytest.raises(DomainError, match="empty_credentials"):
        render_credentials(values, 100)


def test_duplicate_and_unknown_fields_refused():
    with pytest.raises(DomainError, match="duplicate_username"):
        render_credentials([Credential("a", "s"), Credential("a", "t")], 0)
    with pytest.raises(DomainError, match="unsupported_credential_fields"):
        parse_credentials(CREDENTIALS + b'future = true\n')
    with pytest.raises(DomainError, match="duplicate_username"):
        parse_credentials(CREDENTIALS + CREDENTIALS)


@pytest.mark.parametrize("password", ['quote"value', 'newline\n', 'back\\slash', ' leading'])
def test_upstream_credential_mangling_refused(password):
    with pytest.raises(DomainError):
        render_credentials([Credential("user", password)], 0)


def test_capabilities_exact_version():
    assert capabilities("v1.1.0").client_metrics
    assert capabilities("1.1.0").tls_reload
    assert not capabilities("1.1.0").credential_reload
    assert not capabilities("1.2.0").verified
    assert not capabilities("1.1.0-dev").verified


def test_export_command_no_shell_no_prefix_generation():
    command = export_command("/opt/tt/endpoint", "vpn.toml", "hosts.toml", "ivan", "vpn.example:443", "deeplink", "1.1.0")
    assert command == ["/opt/tt/endpoint", "vpn.toml", "hosts.toml", "-c", "ivan", "-a", "vpn.example:443", "--format", "deeplink"]
    with pytest.raises(DomainError):
        export_command("x", "v", "h", "--help", "--help", "toml", "1.1.0")


def test_invalid_toml_redacted():
    with pytest.raises(DomainError) as error:
        parse_document(b'password = "very-sensitive')
    assert "very-sensitive" not in str(error.value)
