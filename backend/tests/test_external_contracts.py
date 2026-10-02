import httpx
import pytest

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.metrics import parse_clients, validate_loopback_listener
from tunnelui.integrations.xui import XUIReadOnlyAdapter, sync_decision


def test_metrics_semantics():
    rows = parse_clients(b'[{"username":"a","ip":"1.2.3.4","sessions":3,"inbound":123,"outbound":456},'
                         b'{"username":"b","ip":null,"sessions":0,"inbound":0,"outbound":0}]')
    assert rows[0].online and not rows[1].online
    assert rows[0].inbound == 123  # upload, not persistent lifetime traffic


@pytest.mark.parametrize("body", [b'{}', b'[{"username":"SECRET","sessions":-1}]', b'not-json'])
def test_metrics_invalid_redacted(body):
    with pytest.raises(DomainError) as exc:
        parse_clients(body)
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("address", ["0.0.0.0:1987", "192.168.1.1:1987", "localhost:1987", "[::]:1987", "127.0.0.1:99999"])
def test_metrics_external_refused(address):
    with pytest.raises(DomainError):
        validate_loopback_listener(address)


def test_metrics_loopback():
    validate_loopback_listener("127.0.0.1:1987")
    validate_loopback_listener("[::1]:1987")


def test_xui_version_pinned_routes_and_secret_projection():
    def handler(request):
        assert request.url.path == "/custom/panel/api/inbounds/list"
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(200, json={"success": True, "obj": [{
            "id": 1, "remark": "Hy2", "protocol": "hysteria2", "port": 8444,
            "enable": True, "settings": "PASSWORD MUST NOT ESCAPE",
        }]})
    adapter = XUIReadOnlyAdapter("https://xui.example/custom", "secret-token", transport=httpx.MockTransport(handler))
    try:
        rows = adapter.list_inbounds()
        assert rows[0].port == 8444 and "PASSWORD" not in str(rows)
    finally:
        adapter.close()


@pytest.mark.parametrize("status", [302, 401, 500])
def test_xui_errors_are_not_missing_clients(status):
    adapter = XUIReadOnlyAdapter("https://xui.example", "secret", transport=httpx.MockTransport(
        lambda _: httpx.Response(status, text="upstream-secret", headers={"Location": "https://evil.example"})
    ))
    try:
        with pytest.raises(DomainError) as exc:
            adapter.find_client("ivan")
        assert "upstream-secret" not in str(exc.value)
    finally:
        adapter.close()


def test_sync_conflicts_no_duplicate_create():
    assert sync_decision("ivan", None, None) == "pending"
    assert sync_decision("ivan", None, 5) == "conflict"
    assert sync_decision("ivan", {"email": "ivan", "id": 5}, None) == "conflict"
    assert sync_decision("ivan", {"email": "ivan", "id": 5}, 5) == "synced"
    assert sync_decision("ivan", {"email": "other", "id": 5}, 5) == "conflict"
