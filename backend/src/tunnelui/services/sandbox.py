from pathlib import Path

from tunnelui.integrations.exporter import FakeTrustTunnelExporter
from tunnelui.security import SecretBox, create_key
from tunnelui.services.adoption import SandboxAdoptionService
from tunnelui.services.apply import SandboxApplyService
from tunnelui.services.coordinator import SandboxEnvironment, TrustTunnelCoordinator
from tunnelui.services.operations import InboundLocks
from tunnelui.system.provider import FakeSystemProvider


class SandboxRuntime:
    """Explicit development composition root; never enabled by production fallback."""

    def __init__(self, root: Path, sessions, key_file: Path):
        root = root.resolve()
        files = {name: root / f"{name}.toml" for name in (
            "vpn", "hosts", "credentials", "rules"
        )}
        if any(not path.is_file() for path in files.values()):
            raise ValueError("sandbox fixture is incomplete")
        if not key_file.exists():
            create_key(key_file)
        self.provider = FakeSystemProvider([True])
        self.locks = InboundLocks()
        self.environment = SandboxEnvironment(
            registry_id="trusttunnel-sandbox.service",
            files=files,
            backup_root=root / "backups",
            provider=self.provider,
            working_directory="/opt/trusttunnel-sandbox",
            exec_start=["/opt/trusttunnel-sandbox/trusttunnel_endpoint", "vpn.toml", "hosts.toml"],
            version="1.1.0",
            public_address="vpn.sandbox.invalid:443",
        )
        low_level = SandboxApplyService(files, self.environment.backup_root, self.provider)
        self.adoption = SandboxAdoptionService(
            low_level, self.environment.registry_id, self.environment.working_directory,
            self.environment.exec_start, self.environment.version,
            self.environment.public_address, self.locks,
        )
        self.coordinator = TrustTunnelCoordinator(
            sessions, SecretBox(key_file),
            {self.environment.registry_id: self.environment}, self.locks,
        )
        self.exporter = FakeTrustTunnelExporter()
