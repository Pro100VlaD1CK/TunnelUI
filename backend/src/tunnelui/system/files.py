import os
import stat
import tempfile
import uuid
from pathlib import Path


def fsync_directory(path: Path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_replace(path: Path, content: bytes):
    """Sandbox primitive, not a production privileged boundary or multi-file transaction."""
    if path.is_symlink() or not path.is_file():
        raise OSError("managed file must be regular")
    info = path.stat()
    if info.st_nlink != 1:
        raise OSError("hardlinked managed file refused")
    fd, name = tempfile.mkstemp(prefix=".tunnelui-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name == "posix":
                os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def prepare_atomic(path: Path, content: bytes, operation_id: str) -> Path:
    """Write and fsync an operation-owned sibling without replacing the target."""
    uuid.UUID(operation_id)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise OSError("managed file must be a regular non-hardlinked file")
    info = path.stat()
    temp = path.parent / f".tunnelui-{operation_id}-{path.name}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name == "posix":
                os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return temp


def commit_atomic(path: Path, temp: Path) -> None:
    if temp.parent != path.parent or not temp.is_file() or temp.is_symlink():
        raise OSError("invalid prepared file")
    os.replace(temp, path)
    fsync_directory(path.parent)


def private_backup(root: Path, operation_id: str, files: dict[str, bytes]) -> Path:
    """Internal sandbox operation; callers generate IDs, never accept them from HTTP."""
    if set(files) - {"vpn", "hosts", "credentials", "rules"}:
        raise ValueError("invalid logical file id")
    import uuid

    uuid.UUID(operation_id)
    directory = root / operation_id
    directory.mkdir(parents=True, mode=0o700)
    for key, content in files.items():
        fd = os.open(directory / key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    fsync_directory(directory)
    fsync_directory(directory.parent)
    return directory
