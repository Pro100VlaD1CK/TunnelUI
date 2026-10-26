"""Read-only, bounded TrustTunnel /clients probe on an explicitly local listener."""

import ipaddress
import time

import httpx
from sqlalchemy.orm import Session

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.metrics import parse_clients
from tunnelui.integrations.trusttunnel import capabilities, parse_document
from tunnelui.models import Inbound
from tunnelui.services.coordinator import TrustTunnelCoordinator

MAX_RESPONSE = 2 * 1024 * 1024


class RuntimeMetricsService:
    def __init__(self, coordinator: TrustTunnelCoordinator,
                 transport: httpx.BaseTransport | None = None):
        self.coordinator = coordinator
        self.transport = transport

    def clients(self, db: Session, inbound_id: str) -> dict:
        inbound = db.get(Inbound, inbound_id)
        if not inbound or inbound.kind != "trusttunnel":
            raise DomainError("inbound_not_found", 404)
        if not capabilities(self.coordinator.environment(inbound).version).client_metrics:
            return {"state": "unsupported", "clients": []}
        try:
            snapshot = self.coordinator.environment(inbound).snapshot()
        except OSError:
            return {"state": "unavailable", "clients": []}
        vpn = parse_document(snapshot["vpn"]).unwrap()
        settings = vpn.get("metrics")
        if not isinstance(settings, dict):
            return {"state": "not_configured", "clients": []}
        if settings.get("per_client_metrics") is not True:
            return {"state": "per_client_disabled", "clients": []}
        address = settings.get("address", "127.0.0.1:1987")
        if not isinstance(address, str):
            return {"state": "unsafe_listener", "clients": []}
        try:
            host, raw_port = address.rsplit(":", 1)
            ip = ipaddress.ip_address(host.strip("[]"))
            port = int(raw_port)
            if not ip.is_loopback or not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            return {"state": "unsafe_listener", "clients": []}
        authority = f"[{ip}]" if ip.version == 6 else str(ip)
        try:
            with httpx.Client(timeout=3, trust_env=False,
                              transport=self.transport) as client:
                with client.stream("GET", f"http://{authority}:{port}/clients") as response:
                    if response.status_code != 200:
                        return {"state": "unavailable", "clients": []}
                    chunks, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_RESPONSE:
                            return {"state": "unavailable", "clients": []}
                        chunks.append(chunk)
        except httpx.HTTPError:
            return {"state": "unavailable", "clients": []}
        try:
            parsed = parse_clients(b"".join(chunks))
        except DomainError:
            return {"state": "unavailable", "clients": []}
        return {
            "state": "available", "observed_at": int(time.time()),
            "counter_scope": "endpoint_process",
            "clients": [item.model_dump() | {"online": item.online} for item in parsed],
        }
