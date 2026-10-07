from pathlib import Path

from tunnelui.agent.client import LinuxAgentClient, UnixSocketTransport
from tunnelui.integrations.exporter import AgentTrustTunnelExporter
from tunnelui.security import SecretBox
from tunnelui.services.coordinator import TrustTunnelCoordinator
from tunnelui.services.linux_adoption import LinuxAdoptionService
from tunnelui.services.operations import InboundLocks
from tunnelui.system.linux import LinuxManagedEnvironment


class LinuxRuntime:
    """Explicit backend composition for a separately installed root agent."""

    def __init__(
        self,
        socket_path: Path,
        managed_id: str,
        version: str,
        public_address: str,
        sessions,
        key_file: Path,
    ):
        client = LinuxAgentClient(UnixSocketTransport(socket_path))
        self.environment = LinuxManagedEnvironment(
            managed_id, client, version, public_address
        )
        self.provider = self.environment.provider
        self.locks = InboundLocks()
        self.coordinator = TrustTunnelCoordinator(
            sessions,
            SecretBox(key_file),
            {managed_id: self.environment},
            self.locks,
        )
        self.exporter = AgentTrustTunnelExporter(client, managed_id)
        self.adoption = LinuxAdoptionService(client, self.environment, self.locks)

