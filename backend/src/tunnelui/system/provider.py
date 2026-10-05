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
        self.running = True

    def restart(self, inbound_id: str):
        self.actions.append(("restart", inbound_id))
        if self.fail_restart:
            raise SystemOperationError("restart_failed")
        self.running = True

    def health(self, inbound_id: str) -> bool:
        self.actions.append(("health", inbound_id))
        return self.running and (self.health_results.pop(0) if self.health_results else True)

    def configure(self, scenario: str) -> None:
        """Select a deterministic local scenario without accepting executable input."""
        if scenario == "healthy":
            self.running, self.fail_restart, self.health_results = True, False, [True]
        elif scenario == "stopped":
            self.running, self.fail_restart, self.health_results = False, False, [False]
        elif scenario == "restart_failure":
            self.running, self.fail_restart, self.health_results = True, True, [True]
        elif scenario == "health_failure_rollback_success":
            self.running, self.fail_restart, self.health_results = True, False, [False, True]
        elif scenario == "rollback_failure":
            self.running, self.fail_restart, self.health_results = True, False, [False, False]
        else:
            raise ValueError("unknown fake scenario")
