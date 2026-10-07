from contextlib import contextmanager

from tunnelui.agent.client import AgentClientError, LinuxAgentClient
from tunnelui.domain.errors import DomainError
from tunnelui.system.provider import LinuxSystemProvider


class LinuxManagedEnvironment:
    """Coordinator environment backed only by an allowlisted agent resource ID."""

    def __init__(
        self,
        registry_id: str,
        client: LinuxAgentClient,
        version: str,
        public_address: str,
    ):
        self.registry_id = registry_id
        self.client = client
        self.version = version
        self.public_address = public_address
        self.provider = LinuxSystemProvider(client, registry_id)

    @contextmanager
    def operation(self, operation_id: str):
        acquired = False
        failed = False
        try:
            self.client.begin(self.registry_id, operation_id)
            acquired = True
            yield
        except AgentClientError as error:
            failed = True
            if error.code == "operation_in_progress":
                raise DomainError("operation_in_progress") from None
            raise OSError(error.code) from None
        except BaseException:
            failed = True
            raise
        finally:
            if acquired:
                try:
                    self.client.end(self.registry_id, operation_id)
                except AgentClientError as error:
                    if not failed:
                        raise OSError(error.code) from None

    def snapshot(self) -> dict[str, bytes]:
        try:
            return self.client.snapshot(self.registry_id)
        except AgentClientError as error:
            raise OSError(error.code) from None

    def create_backup(self, operation_id: str, _snapshot: dict[str, bytes]) -> None:
        try:
            self.client.backup(self.registry_id, operation_id)
        except AgentClientError as error:
            raise OSError(error.code) from None

    def prepare_credentials(self, operation_id: str, content: bytes) -> None:
        try:
            self.client.prepare_credentials(self.registry_id, operation_id, content)
        except AgentClientError as error:
            raise OSError(error.code) from None

    def commit_credentials(self, operation_id: str) -> None:
        try:
            self.client.commit_credentials(self.registry_id, operation_id)
        except AgentClientError as error:
            raise OSError(error.code) from None

    def restore_credentials(self, operation_id: str) -> None:
        try:
            self.client.restore_credentials(self.registry_id, operation_id)
        except AgentClientError as error:
            raise OSError(error.code) from None

    def cleanup(self, operation_id: str) -> None:
        try:
            self.client.cleanup(self.registry_id, operation_id)
        except AgentClientError as error:
            raise OSError(error.code) from None
