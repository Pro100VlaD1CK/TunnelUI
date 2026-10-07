import errno
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from tunnelui.agent.registry import ManagedInstance

LOCK_ID = re.compile(r"^[0-9a-f-]{36}$")


class LockError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class HeldLock:
    descriptor: int
    operation_id: str
    peer_pid: int


class ProcessLocks:
    """Agent-owned nonblocking flock reservations, reclaimed after backend death."""

    def __init__(self, root: Path):
        self.root = root
        self.held: dict[str, HeldLock] = {}

    def acquire(self, instance: ManagedInstance, operation_id: str, peer_pid: int) -> None:
        import fcntl

        if os.name != "posix":
            raise LockError("linux_required")
        if not LOCK_ID.fullmatch(operation_id):
            raise LockError("invalid_operation_id")
        current = self.held.get(instance.id)
        if current and current.operation_id == operation_id and current.peer_pid == peer_pid:
            return
        if current and _pid_alive(current.peer_pid):
            raise LockError("operation_in_progress")
        if current:
            self._drop(instance.id)
        try:
            root_fd = _open_lock_root(self.root)
        except (OSError, LockError):
            raise LockError("unsafe_lock_directory") from None
        try:
            descriptor = os.open(
                f"{instance.id}.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=root_fd,
            )
        except OSError:
            os.close(root_fd)
            raise LockError("lock_failed") from None
        os.close(root_fd)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            os.close(descriptor)
            raise LockError("unsafe_lock_file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise LockError("operation_in_progress") from None
            raise LockError("lock_failed") from None
        self.held[instance.id] = HeldLock(descriptor, operation_id, peer_pid)

    def require(self, instance: ManagedInstance, operation_id: str, peer_pid: int) -> None:
        current = self.held.get(instance.id)
        if not current or current.operation_id != operation_id or current.peer_pid != peer_pid:
            raise LockError("operation_lock_required")

    def release(self, instance: ManagedInstance, operation_id: str, peer_pid: int) -> None:
        if instance.id not in self.held:
            return
        self.require(instance, operation_id, peer_pid)
        self._drop(instance.id)

    def _drop(self, managed_id: str) -> None:
        import fcntl

        current = self.held.pop(managed_id, None)
        if current:
            fcntl.flock(current.descriptor, fcntl.LOCK_UN)
            os.close(current.descriptor)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _open_lock_root(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise LockError("unsafe_lock_directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            if os.geteuid() == 0:
                ancestor = os.fstat(descriptor)
                if ancestor.st_uid != 0 or ancestor.st_mode & 0o022:
                    raise LockError("unsafe_lock_directory")
            following = os.open(
                part, flags | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
        ):
            raise LockError("unsafe_lock_directory")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise

