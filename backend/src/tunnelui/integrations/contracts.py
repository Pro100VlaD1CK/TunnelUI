from typing import Protocol


class TrustTunnelProvider(Protocol):
    def preview(self, inbound_id: str) -> dict: ...
    def export_client(self, inbound_id: str, client_id: str, format: str) -> str: ...


class MetricsProvider(Protocol):
    def clients(self, inbound_id: str) -> list[dict]: ...
