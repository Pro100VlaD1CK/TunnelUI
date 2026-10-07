import asyncio
import base64
import uuid

from tunnelui.agent.exporter import ExportError, TrustTunnelCliExporter
from tunnelui.agent.files import FileSecurityError, SecureManagedFiles
from tunnelui.agent.health import HealthProbe
from tunnelui.agent.locking import LockError, ProcessLocks
from tunnelui.agent.protocol import (
    AgentRequest,
    EmptyArguments,
    ExportArguments,
    OperationArguments,
    PrepareArguments,
    StrictModel,
)
from tunnelui.agent.registry import AgentRegistry, RegistryError
from tunnelui.agent.systemd import SystemdError, SystemdManager


class DispatchError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class AgentDispatcher:
    def __init__(
        self,
        registry: AgentRegistry,
        files: SecureManagedFiles,
        locks: ProcessLocks,
        systemd: SystemdManager,
        health: HealthProbe,
        exporter: TrustTunnelCliExporter,
        service_timeout: float = 15.0,
    ):
        self.registry = registry
        self.files = files
        self.locks = locks
        self.systemd = systemd
        self.health = health
        self.exporter = exporter
        self.service_timeout = service_timeout

    async def dispatch(
        self, request: AgentRequest, arguments: StrictModel, peer_pid: int
    ) -> dict[str, object]:
        try:
            instance = self.registry.get(request.managed_id)
            operation = request.operation
            if operation == "lock.acquire":
                args = _args(arguments, OperationArguments)
                self.locks.acquire(instance, str(args.operation_id), peer_pid)
                return {}
            if operation == "lock.release":
                args = _args(arguments, OperationArguments)
                self.locks.release(instance, str(args.operation_id), peer_pid)
                return {}
            if operation == "files.snapshot":
                args = _args(arguments, OperationArguments)
                self.locks.require(instance, str(args.operation_id), peer_pid)
                return {"files": {
                    key: base64.b64encode(value).decode()
                    for key, value in self.files.snapshot(instance).items()
                }}
            if operation == "service.status":
                _args(arguments, EmptyArguments)
                try:
                    status = await asyncio.wait_for(
                        self.systemd.status(instance.service), self.service_timeout
                    )
                except TimeoutError:
                    raise DispatchError("service_status_timeout") from None
                return {
                    "active_state": status.active_state,
                    "sub_state": status.sub_state,
                    "running": status.running,
                }
            if operation == "health.probe":
                _args(arguments, EmptyArguments)
                return (await self.health.run(instance)).output()
            if operation == "profile.export":
                args = _args(arguments, ExportArguments)
                token = str(uuid.uuid4())
                self.locks.acquire(instance, token, peer_pid)
                try:
                    content = await asyncio.to_thread(
                        self.exporter.export, instance, args.username, args.format
                    )
                finally:
                    self.locks.release(instance, token, peer_pid)
                return {"content_b64": base64.b64encode(content).decode()}

            args = _operation(arguments)
            operation_id = str(args.operation_id)
            self.locks.require(instance, operation_id, peer_pid)
            if operation == "files.backup":
                self.files.create_backup(instance, operation_id)
            elif operation == "files.prepare_credentials":
                prepare = _args(arguments, PrepareArguments)
                self.files.prepare_credentials(instance, operation_id, prepare.content())
            elif operation == "files.commit_credentials":
                self.files.commit_credentials(instance, operation_id)
            elif operation == "files.restore_credentials":
                self.files.restore_credentials(instance, operation_id)
            elif operation == "files.cleanup":
                self.files.cleanup(instance, operation_id)
            elif operation == "service.restart":
                await self.systemd.restart(instance.service, self.service_timeout)
            elif operation == "service.reload":
                if not instance.allow_reload:
                    raise DispatchError("reload_not_allowed")
                await self.systemd.reload(instance.service, self.service_timeout)
            else:
                raise DispatchError("unknown_operation")
            return {}
        except (RegistryError, FileSecurityError, LockError, SystemdError, ExportError) as error:
            raise DispatchError(error.args[0]) from None

def _args(value: StrictModel, expected: type[StrictModel]):
    if not isinstance(value, expected):
        raise DispatchError("malformed_request")
    return value


def _operation(value: StrictModel) -> OperationArguments:
    if not isinstance(value, OperationArguments):
        raise DispatchError("malformed_request")
    return value
