import asyncio
import json
import subprocess
import uuid
from pathlib import Path

import pytest

from tunnelui.agent.client import AgentClientError, LinuxAgentClient
from tunnelui.agent.exporter import ExportError, TrustTunnelCliExporter
from tunnelui.agent.health import HealthProbe
from tunnelui.agent.protocol import (
    MAX_MESSAGE_BYTES,
    MAX_RESPONSE_BYTES,
    AgentResponse,
    ProtocolError,
    decode_request,
    encode_response,
)
from tunnelui.agent.registry import AgentRegistry, ManagedInstance, RegistryError
from tunnelui.agent.server import AgentServer
from tunnelui.agent.systemd import DbusNextSystemdManager, ServiceStatus, SystemdError
from tunnelui.config import Settings
from tunnelui.domain.errors import DomainError
from tunnelui.system.linux import LinuxManagedEnvironment
from tunnelui.system.provider import LinuxSystemProvider, SystemOperationError


def request(**changes):
    value = {
        "version": 1,
        "request_id": str(uuid.uuid4()),
        "managed_id": "primary",
        "operation": "service.status",
        "arguments": {},
    }
    value.update(changes)
    return json.dumps(value).encode()


def registry_text(**changes):
    values = {
        "service": "trusttunnel.service",
        "working_directory": "/opt/trusttunnel",
        "binary": "/opt/trusttunnel/trusttunnel_endpoint",
        "vpn": "/opt/trusttunnel/vpn.toml",
        "hosts": "/opt/trusttunnel/hosts.toml",
        "credentials": "/opt/trusttunnel/credentials.toml",
        "rules": "/opt/trusttunnel/rules.toml",
        "backup_root": "/var/lib/tunnelui-agent/backups/primary",
        "public_address": "vpn.example.invalid:443",
        "expected_version": "1.1.0",
        "health_host": "127.0.0.1",
        "health_port": 443,
        "tls_server_name": "vpn.example.invalid",
    }
    values.update(changes)
    body = "\n".join(
        f'{key} = "{value}"' if isinstance(value, str) else f"{key} = {value}"
        for key, value in values.items()
    )
    return f'backend_user = "tunnelui"\nlock_directory = "/run/tunnelui/locks"\n\n[managed.primary]\n{body}\n'


def test_protocol_rejects_malformed_oversized_version_operation_and_dynamic_fields():
    decoded, arguments = decode_request(request())
    assert decoded.operation == "service.status"
    assert arguments.model_dump() == {}
    cases = [
        (b"{", "malformed_request"),
        (b"x" * (MAX_MESSAGE_BYTES + 1), "request_too_large"),
        (request(version=2), "unsupported_protocol_version"),
        (request(operation="shell.run"), "unknown_operation"),
        (request(arguments={"path": "/etc/shadow"}), "malformed_request"),
        (request(arguments={"service": "ssh.service"}), "malformed_request"),
        (b"\xff\xfe", "malformed_request"),
        (request(managed_id=""), "malformed_request"),
        (request(arguments=[]), "malformed_request"),
        (json.dumps({"version": 1, "operation": "service.status"}).encode(), "malformed_request"),
    ]
    for payload, code in cases:
        with pytest.raises(ProtocolError, match=code):
            decode_request(payload)


class FakeWriter:
    def __init__(self):
        self.output = b""
        self.closed = False

    def get_extra_info(self, _key):
        return object()

    def write(self, value):
        self.output += value

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class FakeDispatcher:
    async def dispatch(self, request, _arguments, peer_pid):
        return {"operation": request.operation, "peer_pid": peer_pid}


def test_response_has_a_hard_size_limit(monkeypatch):
    assert MAX_RESPONSE_BYTES > MAX_MESSAGE_BYTES
    monkeypatch.setattr("tunnelui.agent.protocol.MAX_RESPONSE_BYTES", 256)
    response = AgentResponse(
        request_id=uuid.uuid4(), ok=True, result={"content": "x" * 512}
    )
    with pytest.raises(ProtocolError, match="response_too_large"):
        encode_response(response)


def test_oversized_response_is_typed_and_next_request_still_works(monkeypatch):
    monkeypatch.setattr("tunnelui.agent.server._peer_credentials", lambda _writer: (42, 1000, 1000))
    monkeypatch.setattr("tunnelui.agent.protocol.MAX_RESPONSE_BYTES", 256)

    class VariableDispatcher:
        async def dispatch(self, request, _arguments, _peer_pid):
            return {"content": "x" * (512 if request.managed_id == "large" else 1)}

    async def exchange(managed_id):
        reader = asyncio.StreamReader()
        reader.feed_data(request(managed_id=managed_id) + b"\n")
        reader.feed_eof()
        writer = FakeWriter()
        await AgentServer(VariableDispatcher(), 1000).handle(reader, writer)
        return AgentResponse.model_validate_json(writer.output)

    assert asyncio.run(exchange("large")).error == "response_too_large"
    assert asyncio.run(exchange("small")).ok is True


def test_truncated_ipc_request_is_rejected_without_dispatch(monkeypatch):
    monkeypatch.setattr("tunnelui.agent.server._peer_credentials", lambda _writer: (42, 1000, 1000))

    async def exchange():
        reader = asyncio.StreamReader()
        reader.feed_data(request()[:20])
        reader.feed_eof()
        writer = FakeWriter()
        await AgentServer(FakeDispatcher(), 1000).handle(reader, writer)
        return AgentResponse.model_validate_json(writer.output)

    assert asyncio.run(exchange()).error == "malformed_request"


def test_ipc_rejects_wrong_peer_uid_before_parsing(monkeypatch):
    monkeypatch.setattr("tunnelui.agent.server._peer_credentials", lambda _writer: (42, 1001, 1001))

    async def exchange():
        reader = asyncio.StreamReader()
        reader.feed_data(request() + b"\n")
        reader.feed_eof()
        writer = FakeWriter()
        await AgentServer(FakeDispatcher(), 1000).handle(reader, writer)
        return writer

    writer = asyncio.run(exchange())
    assert writer.closed and writer.output == b""


@pytest.mark.parametrize(
    ("payload", "error"),
    [(b"{", "malformed_request"), (b"x" * (MAX_MESSAGE_BYTES + 1), "request_too_large")],
    ids=("malformed", "oversized"),
)
def test_ipc_server_rejects_malformed_and_oversized_events(monkeypatch, payload, error):
    monkeypatch.setattr("tunnelui.agent.server._peer_credentials", lambda _writer: (42, 1000, 1000))
    async def exchange():
        reader = asyncio.StreamReader(limit=MAX_MESSAGE_BYTES + 2)
        reader.feed_data(payload + b"\n")
        reader.feed_eof()
        writer = FakeWriter()
        await AgentServer(FakeDispatcher(), 1000).handle(reader, writer)
        return writer

    writer = asyncio.run(exchange())
    response = AgentResponse.model_validate_json(writer.output)
    assert response.ok is False
    assert response.error == error
    assert writer.closed is True


def test_registry_rejects_unknown_id_service_and_paths():
    registry = AgentRegistry.parse(registry_text())
    assert registry.get("primary").service == "trusttunnel.service"
    with pytest.raises(RegistryError, match="unknown_managed_id"):
        registry.get("missing")
    for content in (
        registry_text(service="ssh service"),
        registry_text(credentials="/etc/shadow"),
        registry_text(vpn="/opt/trusttunnel/../outside.toml"),
        registry_text(quic_configured="false"),
        registry_text(expected_version="latest"),
        registry_text(health_host="127.0.0.1\nmalicious"),
        registry_text(binary="trusttunnel_endpoint"),
        registry_text(credentials="/opt/trusttunnel/vpn.toml"),
        registry_text(unexpected="value"),
    ):
        with pytest.raises(RegistryError, match="invalid_registry"):
            AgentRegistry.parse(content)
    duplicate = registry_text() + "\n[managed.second]\n" + registry_text().split("[managed.primary]\n", 1)[1]
    with pytest.raises(RegistryError, match="invalid_registry"):
        AgentRegistry.parse(duplicate)


class FakeSystemd:
    def __init__(self, status=ServiceStatus("active", "running"), failure=None):
        self.value, self.failure = status, failure

    async def status(self, _unit):
        if self.failure:
            raise SystemdError(self.failure)
        return self.value


async def succeeds(*_args):
    return None


async def hangs(*_args):
    await asyncio.sleep(1)


class FakeBus:
    def __init__(self):
        self.disconnected = False

    def disconnect(self):
        self.disconnected = True


class FakeDbusManager:
    def __init__(self, result="done", emit=True):
        self.result = result
        self.emit = emit
        self.callback = None

    def on_job_removed(self, callback):
        self.callback = callback

    def off_job_removed(self, callback):
        assert callback is self.callback

    async def call_subscribe(self):
        return None

    async def call_restart_unit(self, unit, mode):
        assert unit == "trusttunnel.service" and mode == "replace"
        if self.emit:
            self.callback(7, "/job/7", unit, self.result)
        return "/job/7"

    async def call_reload_unit(self, unit, mode):
        return await self.call_restart_unit(unit, mode)


def test_systemd_dbus_waits_for_job_and_maps_failure_and_timeout(monkeypatch):
    adapter = DbusNextSystemdManager()

    async def use(fake):
        bus = FakeBus()

        async def manager():
            return bus, fake

        monkeypatch.setattr(adapter, "_manager", manager)
        return bus

    async def success():
        bus = await use(FakeDbusManager())
        await adapter.restart("trusttunnel.service", 0.1)
        assert bus.disconnected

    asyncio.run(success())

    async def failure():
        await use(FakeDbusManager("failed"))
        with pytest.raises(SystemdError, match="service_restart_failed"):
            await adapter.restart("trusttunnel.service", 0.1)

    asyncio.run(failure())

    async def timeout():
        await use(FakeDbusManager(emit=False))
        with pytest.raises(SystemdError, match="service_restart_timeout"):
            await adapter.restart("trusttunnel.service", 0.001)

    asyncio.run(timeout())

    async def connect_timeout():
        async def stalled_manager():
            await asyncio.sleep(1)

        monkeypatch.setattr(adapter, "_manager", stalled_manager)
        with pytest.raises(SystemdError, match="service_restart_timeout"):
            await adapter.restart("trusttunnel.service", 0.001)
        status_adapter = DbusNextSystemdManager(status_timeout=0.001)
        monkeypatch.setattr(status_adapter, "_manager", stalled_manager)
        with pytest.raises(SystemdError, match="service_status_timeout"):
            await status_adapter.status("trusttunnel.service")

    asyncio.run(connect_timeout())


@pytest.mark.parametrize("stage", ["get_unit", "introspect", "property"])
def test_systemd_status_bounds_dbus_calls(monkeypatch, stage):
    class Properties:
        async def call_get(self, _interface, _name):
            if stage == "property":
                await asyncio.sleep(1)
            return type("Value", (), {"value": "active"})()

    class Proxy:
        def get_interface(self, _name):
            return Properties()

    class Bus(FakeBus):
        async def introspect(self, _name, _path):
            if stage == "introspect":
                await asyncio.sleep(1)
            return object()

        def get_proxy_object(self, _name, _path, _introspection):
            return Proxy()

    class Manager:
        async def call_get_unit(self, _unit):
            if stage == "get_unit":
                await asyncio.sleep(1)
            return "/unit/1"

    adapter = DbusNextSystemdManager(status_timeout=0.001)
    bus = Bus()

    async def manager():
        return bus, Manager()

    monkeypatch.setattr(adapter, "_manager", manager)
    with pytest.raises(SystemdError, match="service_status_timeout"):
        asyncio.run(adapter.status("trusttunnel.service"))
    assert bus.disconnected


def instance():
    root = Path("/opt/trusttunnel")
    return ManagedInstance(
        id="primary", service="trusttunnel.service", working_directory=root,
        binary=root / "trusttunnel_endpoint",
        files={key: root / f"{key}.toml" for key in ("vpn", "hosts", "credentials", "rules")},
        backup_root=Path("/var/lib/tunnelui-agent/backups/primary"),
        public_address="vpn.example.invalid:443", expected_version="1.1.0",
        health_host="127.0.0.1", health_port=443,
        tls_server_name="vpn.example.invalid", quic_configured=True,
        allow_reload=False, owner_uid=0,
    )


def test_health_is_bounded_and_does_not_claim_quic_verification():
    result = asyncio.run(HealthProbe(FakeSystemd(), succeeds, succeeds).run(instance(), 0.2))
    assert result.healthy is True
    assert result.udp_quic.status == "unverified"
    timeout = asyncio.run(HealthProbe(FakeSystemd(), hangs, succeeds).run(instance(), 0.01))
    assert timeout.healthy is False
    assert timeout.service.detail == "health_timeout"


class RecordingTransport:
    def __init__(self):
        self.requests = []

    def exchange(self, payload: bytes) -> bytes:
        value = json.loads(payload)
        self.requests.append(value)
        result = {"healthy": True} if value["operation"] == "health.probe" else {}
        return json.dumps({
            "version": 1, "request_id": value["request_id"], "ok": True,
            "result": result, "error": None,
        }).encode()


def test_linux_client_sends_only_managed_id_and_typed_operations():
    transport = RecordingTransport()
    client = LinuxAgentClient(transport)
    operation_id = str(uuid.uuid4())
    client.begin("primary", operation_id)
    client.restart("primary")
    assert client.health("primary")["healthy"] is True
    client.end("primary", operation_id)
    assert [item["operation"] for item in transport.requests] == [
        "lock.acquire", "service.restart", "health.probe", "lock.release"
    ]
    assert all("path" not in json.dumps(item) and "service" not in item["arguments"]
               for item in transport.requests)
    with pytest.raises(AgentClientError, match="operation_lock_required"):
        client.restart("primary")


class RestartErrorTransport(RecordingTransport):
    def exchange(self, payload: bytes) -> bytes:
        value = json.loads(payload)
        if value["operation"] == "service.restart":
            return json.dumps({
                "version": 1, "request_id": value["request_id"], "ok": False,
                "result": None, "error": "service_restart_failed",
            }).encode()
        return super().exchange(payload)


def test_linux_system_provider_maps_restart_error_without_details():
    client = LinuxAgentClient(RestartErrorTransport())
    operation_id = str(uuid.uuid4())
    client.begin("primary", operation_id)
    with pytest.raises(SystemOperationError, match="service_restart_failed"):
        LinuxSystemProvider(client, "primary").restart("ignored-database-id")
    client.end("primary", operation_id)


class LockConflictTransport(RecordingTransport):
    def exchange(self, payload: bytes) -> bytes:
        value = json.loads(payload)
        return json.dumps({
            "version": 1, "request_id": value["request_id"], "ok": False,
            "result": None, "error": "operation_in_progress",
        }).encode()


def test_linux_environment_maps_os_lock_conflict_to_domain_error():
    environment = LinuxManagedEnvironment(
        "primary", LinuxAgentClient(LockConflictTransport()), "1.1.0", "vpn.invalid:443"
    )
    with pytest.raises(DomainError, match="operation_in_progress"):
        with environment.operation(str(uuid.uuid4())):
            pytest.fail("lock conflict must stop the operation")


def test_linux_environment_releases_lock_after_success_and_failure():
    transport = RecordingTransport()
    environment = LinuxManagedEnvironment(
        "primary", LinuxAgentClient(transport), "1.1.0", "vpn.invalid:443"
    )
    with environment.operation(str(uuid.uuid4())):
        pass
    with pytest.raises(RuntimeError, match="fixture_failure"):
        with environment.operation(str(uuid.uuid4())):
            raise RuntimeError("fixture_failure")
    assert [item["operation"] for item in transport.requests] == [
        "lock.acquire", "lock.release", "lock.acquire", "lock.release"
    ]


def test_agent_settings_are_explicit_complete_and_not_sandbox(tmp_path):
    common = {
        "origin": "https://panel.invalid",
        "agent_socket": tmp_path / "agent.sock",
        "agent_managed_id": "primary",
        "agent_expected_version": "1.1.0",
        "agent_public_address": "vpn.invalid:443",
    }
    assert Settings(**common).agent_managed_id == "primary"
    with pytest.raises(ValueError, match="configured together"):
        Settings(origin="https://panel.invalid", agent_socket=tmp_path / "agent.sock")
    with pytest.raises(ValueError, match="mutually exclusive"):
        Settings(**common, development=True)


def test_export_failure_does_not_expose_argv_or_output(monkeypatch):
    exporter = TrustTunnelCliExporter(object())

    def fail(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["binary", "super-secret"], 1, output=b"secret-output")

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(ExportError) as captured:
        exporter._run(7, 8, ["-c", "alice"], 1.0)
    assert str(captured.value) == "profile_export_timeout"
    assert "secret" not in str(captured.value)



def test_managed_describe_rejects_client_supplied_dynamic_fields():
    decoded, arguments = decode_request(request(operation="managed.describe"))
    assert decoded.operation == "managed.describe"
    assert arguments.model_dump() == {}

    with pytest.raises(ProtocolError, match="malformed_request"):
        decode_request(
            request(
                operation="managed.describe",
                arguments={"path": "/etc/shadow"},
            )
        )


def test_linux_client_describe_uses_only_allowlisted_managed_id():
    transport = RecordingTransport()
    client = LinuxAgentClient(transport)

    assert client.describe("primary") == {}

    sent = transport.requests[-1]
    assert sent["managed_id"] == "primary"
    assert sent["operation"] == "managed.describe"
    assert sent["arguments"] == {}


def test_health_retries_during_endpoint_startup():
    attempts = 0

    async def starting_tcp(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("not ready yet")

    result = asyncio.run(
        HealthProbe(
            FakeSystemd(),
            starting_tcp,
            succeeds,
        ).run(instance(), 1.0)
    )

    assert result.healthy is True
    assert attempts == 2
    assert result.tcp.status == "ok"
    assert result.tls.status == "ok"


def test_deeplink_export_discards_cli_explanatory_text(monkeypatch):
    exporter = TrustTunnelCliExporter(object())

    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 0, b"trusttunnel_endpoint 1.1.0"
        if calls == 2:
            return 0, b"-c -a --format deeplink toml"
        return 0, (
            b"tt://?VALID_PAYLOAD\n\n"
            b"To connect on mobile, scan the QR page\n"
        )

    class OpenBinary:
        def __enter__(self):
            return 7, 8, type(
                "Info",
                (),
                {
                    "st_ino": 1,
                    "st_mtime_ns": 1,
                    "st_ctime_ns": 1,
                    "st_size": 1,
                },
            )()

        def __exit__(self, *_args):
            return False

    class Files:
        def open_binary(self, _instance):
            return OpenBinary()

    exporter.files = Files()
    monkeypatch.setattr(exporter, "_run", fake_run)

    result = exporter.export(instance(), "alice", "deeplink")

    assert result == b"tt://?VALID_PAYLOAD"
    assert b"\n" not in result
