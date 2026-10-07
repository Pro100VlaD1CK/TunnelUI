import asyncio
from dataclasses import dataclass
from typing import Protocol


class SystemdError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ServiceStatus:
    active_state: str
    sub_state: str

    @property
    def running(self) -> bool:
        return self.active_state == "active" and self.sub_state == "running"


class SystemdManager(Protocol):
    async def status(self, unit: str) -> ServiceStatus: ...
    async def restart(self, unit: str, timeout: float) -> None: ...
    async def reload(self, unit: str, timeout: float) -> None: ...


class DbusNextSystemdManager:
    """org.freedesktop.systemd1 adapter; imports dbus-next only on Linux use."""

    def __init__(self, status_timeout: float = 5.0):
        self.status_timeout = status_timeout

    async def status(self, unit: str) -> ServiceStatus:
        bus = None
        try:
            async with asyncio.timeout(self.status_timeout):
                bus, manager = await self._manager()
                path = await manager.call_get_unit(unit)
                introspection = await bus.introspect("org.freedesktop.systemd1", path)
                proxy = bus.get_proxy_object("org.freedesktop.systemd1", path, introspection)
                properties = proxy.get_interface("org.freedesktop.DBus.Properties")
                active = await properties.call_get("org.freedesktop.systemd1.Unit", "ActiveState")
                sub = await properties.call_get("org.freedesktop.systemd1.Unit", "SubState")
                return ServiceStatus(str(active.value), str(sub.value))
        except TimeoutError:
            raise SystemdError("service_status_timeout") from None
        except Exception as error:
            raise SystemdError("service_status_failed") from error
        finally:
            if bus is not None:
                bus.disconnect()

    async def restart(self, unit: str, timeout: float) -> None:
        await self._job(unit, "restart", timeout)

    async def reload(self, unit: str, timeout: float) -> None:
        await self._job(unit, "reload", timeout)

    async def _job(self, unit: str, action: str, timeout: float) -> None:
        bus = None
        manager = None
        removed = None
        try:
            async with asyncio.timeout(timeout):
                bus, manager = await self._manager()
                result = asyncio.get_running_loop().create_future()
                expected_path: str | None = None
                early: dict[str, str] = {}

                def removed(_job_id, path, _unit, job_result):
                    nonlocal expected_path
                    if _unit != unit:
                        return
                    if expected_path == path and not result.done():
                        result.set_result(str(job_result))
                    elif expected_path is None and len(early) < 64:
                        early[str(path)] = str(job_result)

                manager.on_job_removed(removed)
                await manager.call_subscribe()
                call = (
                    manager.call_restart_unit
                    if action == "restart"
                    else manager.call_reload_unit
                )
                expected_path = str(await call(unit, "replace"))
                if expected_path in early and not result.done():
                    result.set_result(early[expected_path])
                if await result != "done":
                    raise SystemdError(f"service_{action}_failed")
        except TimeoutError:
            raise SystemdError(f"service_{action}_timeout") from None
        except SystemdError:
            raise
        except Exception as error:
            raise SystemdError(f"service_{action}_failed") from error
        finally:
            if manager is not None and removed is not None:
                try:
                    manager.off_job_removed(removed)
                except Exception:
                    pass
            if bus is not None:
                bus.disconnect()

    @staticmethod
    async def _manager():
        bus = None
        try:
            from dbus_next import BusType
            from dbus_next.aio import MessageBus

            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            introspection = await bus.introspect(
                "org.freedesktop.systemd1", "/org/freedesktop/systemd1"
            )
            proxy = bus.get_proxy_object(
                "org.freedesktop.systemd1", "/org/freedesktop/systemd1", introspection
            )
            return bus, proxy.get_interface("org.freedesktop.systemd1.Manager")
        except asyncio.CancelledError:
            if bus is not None:
                bus.disconnect()
            raise
        except Exception as error:
            if bus is not None:
                bus.disconnect()
            raise SystemdError("systemd_dbus_unavailable") from error

