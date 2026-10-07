import argparse
import asyncio
import os
from pathlib import Path

from tunnelui.agent.dispatcher import AgentDispatcher
from tunnelui.agent.exporter import TrustTunnelCliExporter
from tunnelui.agent.files import SecureManagedFiles
from tunnelui.agent.health import HealthProbe
from tunnelui.agent.locking import ProcessLocks
from tunnelui.agent.protocol import MAX_MESSAGE_BYTES
from tunnelui.agent.registry import AgentRegistry
from tunnelui.agent.server import AgentServer, activated_socket
from tunnelui.agent.systemd import DbusNextSystemdManager


def main() -> None:
    parser = argparse.ArgumentParser(prog="tunnelui-agent")
    parser.add_argument("--config", type=Path, default=Path("/etc/tunnelui/agent.toml"))
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("tunnelui-agent must run as root on Linux")
    asyncio.run(run(args.config))


async def run(config_path: Path) -> None:
    import pwd

    registry = AgentRegistry.load(config_path)
    allowed_uid = pwd.getpwnam(registry.backend_user).pw_uid
    files = SecureManagedFiles()
    systemd = DbusNextSystemdManager()
    dispatcher = AgentDispatcher(
        registry=registry,
        files=files,
        locks=ProcessLocks(registry.lock_directory),
        systemd=systemd,
        health=HealthProbe(systemd),
        exporter=TrustTunnelCliExporter(files),
    )
    sock = activated_socket()
    sock.setblocking(False)
    server = await asyncio.start_unix_server(
        AgentServer(dispatcher, allowed_uid).handle,
        sock=sock,
        limit=MAX_MESSAGE_BYTES + 2,
    )
    async with server:
        await server.serve_forever()

