import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import tomlkit

from tunnelui.agent.protocol import MANAGED_ID

UNIT_NAME = re.compile(r"^[A-Za-z0-9_.@-]+\.service$")
BACKEND_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
HOST = re.compile(r"^[A-Za-z0-9.:_-]{1,255}$")
FILE_IDS = ("vpn", "hosts", "credentials", "rules")
MANAGED_FIELDS = {
    "service", "working_directory", "binary", *FILE_IDS, "backup_root",
    "public_address", "expected_version", "health_host", "health_port",
    "tls_server_name", "quic_configured", "allow_reload", "owner_uid",
}


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class ManagedInstance:
    id: str
    service: str
    working_directory: Path
    binary: Path
    files: dict[str, Path]
    backup_root: Path
    public_address: str
    expected_version: str
    health_host: str
    health_port: int
    tls_server_name: str
    quic_configured: bool
    allow_reload: bool
    owner_uid: int = 0


@dataclass(frozen=True)
class AgentRegistry:
    backend_user: str
    lock_directory: Path
    managed: dict[str, ManagedInstance]

    def get(self, managed_id: str) -> ManagedInstance:
        try:
            return self.managed[managed_id]
        except KeyError:
            raise RegistryError("unknown_managed_id") from None

    @classmethod
    def load(cls, path: Path) -> "AgentRegistry":
        if os.name != "posix":
            raise RegistryError("linux_required")
        if not path.is_absolute() or ".." in path.parts:
            raise RegistryError("unsafe_registry_file")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        directory = os.open("/", flags)
        try:
            for part in path.parent.parts[1:]:
                _root_directory(directory)
                following = os.open(part, flags, dir_fd=directory)
                os.close(directory)
                directory = following
            _root_directory(directory)
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory,
            )
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 1024 * 1024
                ):
                    raise RegistryError("unsafe_registry_file")
                chunks = []
                remaining = 1024 * 1024 + 1
                while remaining:
                    chunk = os.read(descriptor, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if remaining == 0:
                    raise RegistryError("unsafe_registry_file")
                content = b"".join(chunks)
            finally:
                os.close(descriptor)
        except OSError:
            raise RegistryError("unsafe_registry_file") from None
        finally:
            os.close(directory)
        try:
            return cls.parse(content.decode("utf-8"))
        except UnicodeDecodeError:
            raise RegistryError("invalid_registry") from None

    @classmethod
    def parse(cls, content: str) -> "AgentRegistry":
        try:
            document = tomlkit.parse(content)
            if set(document) != {"backend_user", "lock_directory", "managed"}:
                raise RegistryError("invalid_registry")
            backend_user = _simple(document, "backend_user", str)
            if not BACKEND_USER.fullmatch(backend_user):
                raise RegistryError("invalid_backend_user")
            lock_directory = _absolute(_simple(document, "lock_directory", str))
            tables = document.get("managed")
            if not isinstance(tables, Mapping) or not tables:
                raise RegistryError("managed_registry_required")
            managed = {
                item_id: _managed(item_id, table)
                for item_id, table in tables.items()
            }
            services = [item.service for item in managed.values()]
            paths = [path for item in managed.values() for path in item.files.values()]
            if len(services) != len(set(services)) or len(paths) != len(set(paths)):
                raise RegistryError("duplicate_managed_resource")
        except (RegistryError, KeyError, TypeError, ValueError, tomlkit.exceptions.ParseError):
            raise RegistryError("invalid_registry") from None
        return cls(backend_user=backend_user, lock_directory=lock_directory, managed=managed)


def _simple(mapping, key: str, expected: type):
    value = mapping[key]
    if not isinstance(value, expected):
        raise RegistryError("invalid_registry")
    return value


def _root_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RegistryError("unsafe_registry_directory")


def _absolute(value: str) -> Path:
    pure = PurePosixPath(value)
    if not pure.is_absolute() or ".." in pure.parts or not pure.name:
        raise RegistryError("invalid_path")
    return Path(value)


def _optional_bool(mapping, key: str, default: bool) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        raise RegistryError("invalid_registry")
    return value


def _managed(item_id: str, table) -> ManagedInstance:
    if not MANAGED_ID.fullmatch(item_id):
        raise RegistryError("invalid_managed_id")
    if not isinstance(table, Mapping) or set(table) - MANAGED_FIELDS:
        raise RegistryError("invalid_managed_fields")
    service = _simple(table, "service", str)
    if not UNIT_NAME.fullmatch(service):
        raise RegistryError("invalid_service")
    workdir = _absolute(_simple(table, "working_directory", str))
    binary = _absolute(_simple(table, "binary", str))
    files = {key: _absolute(_simple(table, key, str)) for key in FILE_IDS}
    for candidate in (binary, *files.values()):
        if candidate.parent != workdir:
            raise RegistryError("path_outside_working_directory") from None
    if len(set(files.values())) != len(files) or binary in files.values():
        raise RegistryError("duplicate_managed_path")
    public_address = _simple(table, "public_address", str)
    if not public_address or len(public_address) > 255 or any(ord(char) < 32 for char in public_address):
        raise RegistryError("invalid_public_address")
    health_port = _simple(table, "health_port", int)
    if isinstance(health_port, bool) or not 1 <= health_port <= 65535:
        raise RegistryError("invalid_health_port")
    owner_uid = table.get("owner_uid", 0)
    if isinstance(owner_uid, bool) or not isinstance(owner_uid, int) or owner_uid < 0:
        raise RegistryError("invalid_owner_uid")
    expected_version = _simple(table, "expected_version", str)
    health_host = _simple(table, "health_host", str)
    tls_server_name = _simple(table, "tls_server_name", str)
    if not VERSION.fullmatch(expected_version):
        raise RegistryError("invalid_expected_version")
    if not HOST.fullmatch(health_host) or not HOST.fullmatch(tls_server_name):
        raise RegistryError("invalid_health_target")
    return ManagedInstance(
        id=item_id,
        service=service,
        working_directory=workdir,
        binary=binary,
        files=files,
        backup_root=_absolute(_simple(table, "backup_root", str)),
        public_address=public_address,
        expected_version=expected_version,
        health_host=health_host,
        health_port=health_port,
        tls_server_name=tls_server_name,
        quic_configured=_optional_bool(table, "quic_configured", True),
        allow_reload=_optional_bool(table, "allow_reload", False),
        owner_uid=owner_uid,
    )

