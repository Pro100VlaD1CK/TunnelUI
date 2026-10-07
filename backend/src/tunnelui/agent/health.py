import asyncio
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

from tunnelui.agent.registry import ManagedInstance
from tunnelui.agent.systemd import SystemdManager


@dataclass(frozen=True)
class ProbeResult:
    status: str
    detail: str


@dataclass(frozen=True)
class HealthResult:
    healthy: bool
    service: ProbeResult
    tcp: ProbeResult
    tls: ProbeResult
    udp_quic: ProbeResult

    def output(self) -> dict[str, object]:
        return asdict(self)


class HealthProbe:
    def __init__(
        self,
        systemd: SystemdManager,
        tcp_probe: Callable[[str, int, float], Awaitable[None]] | None = None,
        tls_probe: Callable[[str, int, str, float], Awaitable[None]] | None = None,
    ):
        self.systemd = systemd
        self.tcp_probe = tcp_probe or _tcp
        self.tls_probe = tls_probe or _tls

    async def run(self, instance: ManagedInstance, timeout: float = 5.0) -> HealthResult:
        try:
            async with asyncio.timeout(timeout):
                service = await self.systemd.status(instance.service)
                service_result = ProbeResult(
                    "ok" if service.running else "failed",
                    f"{service.active_state}/{service.sub_state}",
                )
                tcp_result = await self._one(
                    self.tcp_probe(instance.health_host, instance.health_port, timeout),
                    "tcp_unreachable",
                )
                tls_result = await self._one(
                    self.tls_probe(
                        instance.health_host,
                        instance.health_port,
                        instance.tls_server_name,
                        timeout,
                    ),
                    "tls_handshake_failed",
                )
        except TimeoutError:
            return HealthResult(
                False,
                ProbeResult("unknown", "health_timeout"),
                ProbeResult("unknown", "health_timeout"),
                ProbeResult("unknown", "health_timeout"),
                _quic(instance),
            )
        healthy = all(item.status == "ok" for item in (service_result, tcp_result, tls_result))
        return HealthResult(healthy, service_result, tcp_result, tls_result, _quic(instance))

    @staticmethod
    async def _one(awaitable, error_code: str) -> ProbeResult:
        try:
            await awaitable
        except (OSError, ssl.SSLError, TimeoutError):
            return ProbeResult("failed", error_code)
        return ProbeResult("ok", "reachable")


async def _tcp(host: str, port: int, timeout: float) -> None:
    _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    writer.close()
    await writer.wait_closed()


async def _tls(host: str, port: int, server_name: str, timeout: float) -> None:
    context = ssl.create_default_context()
    _reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=context, server_hostname=server_name), timeout
    )
    writer.close()
    await writer.wait_closed()


def _quic(instance: ManagedInstance) -> ProbeResult:
    if not instance.quic_configured:
        return ProbeResult("disabled", "not_configured")
    # UDP connect/send cannot prove that a QUIC endpoint received or accepted a packet.
    return ProbeResult("unverified", "configured_but_no_credential_free_probe")

