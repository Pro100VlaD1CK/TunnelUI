from typing import Protocol


class SystemOperationError(Exception):
    pass


class SystemProvider(Protocol):
    def restart(self, inbound_id: str) -> None: ...
    def health(self, inbound_id: str) -> bool: ...


class FakeSystemProvider:
    """Explicit test provider. Never launches processes or connects to a host."""

    def __init__(self, health_results: list[bool] | None = None):
        self.health_results = list(health_results if health_results is not None else [True])
        self.actions: list[tuple[str, str]] = []
        self.fail_restart = False

    def restart(self, inbound_id: str):
        self.actions.append(("restart", inbound_id))
        if self.fail_restart:
            raise SystemOperationError("restart_failed")

    def health(self, inbound_id: str) -> bool:
        self.actions.append(("health", inbound_id))
        return self.health_results.pop(0) if self.health_results else False
