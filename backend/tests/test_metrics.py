import pytest

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.metrics import parse_clients, validate_loopback_listener


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
