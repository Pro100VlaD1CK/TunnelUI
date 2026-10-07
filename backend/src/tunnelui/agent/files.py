import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from tunnelui.agent.registry import FILE_IDS, ManagedInstance

MAX_MANAGED_FILE_BYTES = 4 * 1024 * 1024
MAX_BINARY_BYTES = 64 * 1024 * 1024


class FileSecurityError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SecureManagedFiles:
    """Descriptor-relative file operations for one root-owned allowlisted instance."""

    def snapshot(self, instance: ManagedInstance) -> dict[str, bytes]:
        return {key: self._read_managed(instance, key) for key in FILE_IDS}

    def create_backup(self, instance: ManagedInstance, operation_id: str) -> None:
        _operation_id(operation_id)
        root_fd = self._open_directory(instance.backup_root, instance.owner_uid, private=True)
        try:
            try:
                os.mkdir(operation_id, 0o700, dir_fd=root_fd)
            except FileExistsError:
                raise FileSecurityError("backup_already_exists") from None
            backup_fd = os.open(
                operation_id,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _cloexec(),
                dir_fd=root_fd,
            )
            try:
                self._validate_directory_fd(backup_fd, instance.owner_uid, private=True)
                for key in FILE_IDS:
                    content = self._read_managed(instance, key)
                    output = os.open(
                        key,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _cloexec(),
                        0o600,
                        dir_fd=backup_fd,
                    )
                    try:
                        _write_all(output, content)
                        os.fsync(output)
                    finally:
                        os.close(output)
                os.fsync(backup_fd)
            finally:
                os.close(backup_fd)
            os.fsync(root_fd)
        except BaseException:
            # A partial backup is deliberately retained for operator inspection.
            raise
        finally:
            os.close(root_fd)

    def prepare_credentials(
        self, instance: ManagedInstance, operation_id: str, content: bytes
    ) -> None:
        _operation_id(operation_id)
        if not content or len(content) > MAX_MANAGED_FILE_BYTES:
            raise FileSecurityError("invalid_managed_content")
        path = instance.files["credentials"]
        with self._parent(instance, path) as parent_fd:
            current_fd = self._open_managed_fd(instance, "credentials", parent_fd)
            try:
                current = os.fstat(current_fd)
            finally:
                os.close(current_fd)
            name = _prepared_name(operation_id)
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _cloexec(),
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.fchmod(descriptor, stat.S_IMODE(current.st_mode))
                if os.fstat(descriptor).st_uid != instance.owner_uid:
                    os.fchown(descriptor, instance.owner_uid, current.st_gid)
                _write_all(descriptor, content)
                os.fsync(descriptor)
            except BaseException:
                os.close(descriptor)
                os.unlink(name, dir_fd=parent_fd)
                raise
            else:
                os.close(descriptor)
            os.fsync(parent_fd)

    def commit_credentials(self, instance: ManagedInstance, operation_id: str) -> None:
        _operation_id(operation_id)
        path = instance.files["credentials"]
        with self._parent(instance, path) as parent_fd:
            current_fd = self._open_managed_fd(instance, "credentials", parent_fd)
            os.close(current_fd)
            prepared = _prepared_name(operation_id)
            temp_fd = os.open(
                prepared, os.O_RDONLY | os.O_NOFOLLOW | _cloexec(), dir_fd=parent_fd
            )
            try:
                self._validate_regular_fd(temp_fd, instance.owner_uid, credentials=True)
            finally:
                os.close(temp_fd)
            os.replace(prepared, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)

    def restore_credentials(self, instance: ManagedInstance, operation_id: str) -> None:
        _operation_id(operation_id)
        root_fd = self._open_directory(instance.backup_root, instance.owner_uid, private=True)
        try:
            backup_fd = os.open(
                operation_id,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _cloexec(),
                dir_fd=root_fd,
            )
            try:
                self._validate_directory_fd(backup_fd, instance.owner_uid, private=True)
                source = os.open(
                    "credentials", os.O_RDONLY | os.O_NOFOLLOW | _cloexec(), dir_fd=backup_fd
                )
                try:
                    self._validate_regular_fd(source, instance.owner_uid, credentials=True)
                    content = _read_bounded(source)
                finally:
                    os.close(source)
            finally:
                os.close(backup_fd)
        finally:
            os.close(root_fd)
        # A crash after prepare may leave this operation's old temp file behind.
        self.cleanup(instance, operation_id)
        self.prepare_credentials(instance, operation_id, content)
        self.commit_credentials(instance, operation_id)

    def cleanup(self, instance: ManagedInstance, operation_id: str) -> None:
        _operation_id(operation_id)
        path = instance.files["credentials"]
        with self._parent(instance, path) as parent_fd:
            try:
                os.unlink(_prepared_name(operation_id), dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.fsync(parent_fd)

    @contextmanager
    def open_binary(self, instance: ManagedInstance):
        with self._parent(instance, instance.binary) as parent_fd:
            descriptor = os.open(
                instance.binary.name,
                os.O_RDONLY | os.O_NOFOLLOW | _cloexec(),
                dir_fd=parent_fd,
            )
            try:
                info = self._validate_regular_fd(
                    descriptor,
                    instance.owner_uid,
                    max_bytes=MAX_BINARY_BYTES,
                    size_error="binary_too_large",
                )
                if not info.st_mode & stat.S_IXUSR:
                    raise FileSecurityError("binary_not_executable")
                yield descriptor, parent_fd, info
            finally:
                os.close(descriptor)

    def _read_managed(self, instance: ManagedInstance, key: str) -> bytes:
        path = instance.files[key]
        with self._parent(instance, path) as parent_fd:
            descriptor = self._open_managed_fd(instance, key, parent_fd)
            try:
                return _read_bounded(descriptor)
            finally:
                os.close(descriptor)

    @contextmanager
    def _parent(self, instance: ManagedInstance, path: Path):
        if path.parent != instance.working_directory:
            raise FileSecurityError("path_outside_working_directory")
        descriptor = self._open_directory(path.parent, instance.owner_uid)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def _open_directory(self, path: Path, owner_uid: int, private: bool = False) -> int:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise FileSecurityError("linux_required")
        if not path.is_absolute() or ".." in path.parts:
            raise FileSecurityError("unsafe_managed_directory")
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | _cloexec())
        try:
            for part in path.parts[1:]:
                if owner_uid == 0:
                    ancestor = os.fstat(descriptor)
                    if ancestor.st_uid != 0 or ancestor.st_mode & 0o022:
                        raise FileSecurityError("unsafe_managed_directory")
                following = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _cloexec(),
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = following
            self._validate_directory_fd(descriptor, owner_uid, private)
        except (OSError, FileSecurityError):
            os.close(descriptor)
            raise FileSecurityError("unsafe_managed_directory") from None
        return descriptor

    @staticmethod
    def _validate_directory_fd(descriptor: int, owner_uid: int, private: bool = False):
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid:
            raise FileSecurityError("unsafe_managed_directory")
        if info.st_mode & (0o077 if private else 0o022):
            raise FileSecurityError("unsafe_directory_mode")
        return info

    def _open_managed_fd(self, instance: ManagedInstance, key: str, parent_fd: int) -> int:
        try:
            descriptor = os.open(
                instance.files[key].name,
                os.O_RDONLY | os.O_NOFOLLOW | _cloexec(),
                dir_fd=parent_fd,
            )
        except OSError:
            raise FileSecurityError("unsafe_managed_file") from None
        try:
            self._validate_regular_fd(
                descriptor, instance.owner_uid, credentials=key == "credentials"
            )
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _validate_regular_fd(
        descriptor: int,
        owner_uid: int,
        credentials: bool = False,
        max_bytes: int = MAX_MANAGED_FILE_BYTES,
        size_error: str = "managed_file_too_large",
    ):
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != owner_uid:
            raise FileSecurityError("unsafe_managed_file")
        if info.st_mode & 0o022 or (credentials and info.st_mode & 0o077):
            raise FileSecurityError("unsafe_file_mode")
        if info.st_size > max_bytes:
            raise FileSecurityError(size_error)
        return info


def _operation_id(value: str) -> None:
    try:
        uuid.UUID(value)
    except ValueError:
        raise FileSecurityError("invalid_operation_id") from None


def _prepared_name(operation_id: str) -> str:
    return f".tunnelui-{operation_id}-credentials.tmp"


def _cloexec() -> int:
    return getattr(os, "O_CLOEXEC", 0)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise FileSecurityError("write_failed")
        view = view[written:]


def _read_bounded(descriptor: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, MAX_MANAGED_FILE_BYTES + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_MANAGED_FILE_BYTES:
            raise FileSecurityError("managed_file_too_large")

