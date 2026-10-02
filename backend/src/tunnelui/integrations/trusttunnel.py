import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

import tomlkit
from tomlkit.exceptions import ParseError

from tunnelui.domain.errors import DomainError


@dataclass(frozen=True)
class Capabilities:
    version: str
    verified: bool
    client_metrics: bool = False
    export_deeplink: bool = False
    tls_reload: bool = False
    credential_reload: bool = False


def capabilities(version: str) -> Capabilities:
    normalized = version.strip().removeprefix("v")
    return Capabilities(normalized, normalized == "1.1.0", *([True] * 3 if normalized == "1.1.0" else [False] * 3))


def source_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_config_path(working_directory: str, value: str) -> str:
    """Lexical POSIX resolution; allowlist + filesystem checks belong to the agent."""
    base = PurePosixPath(working_directory)
    if not base.is_absolute() or not value or "\x00" in value:
        raise DomainError("invalid_config_path")
    path = PurePosixPath(value)
    parts: list[str] = []
    for part in (path if path.is_absolute() else base / path).parts:
        if part == "/":
            continue
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/" + "/".join(parts)


def parse_document(content: bytes):
    try:
        return tomlkit.parse(content.decode("utf-8"))
    except (ParseError, UnicodeError, ValueError):
        raise DomainError("invalid_toml") from None


def preview(working_directory: str, exec_start: list[str], files: dict[str, bytes], version: str):
    """Consume structured provider data, never shell/unit text. Secret-free output."""
    if len(exec_start) != 3 or not PurePosixPath(exec_start[0]).is_absolute():
        raise DomainError("unsupported_exec_start")
    vpn = parse_document(files["vpn"])
    hosts = parse_document(files["hosts"])
    credentials = parse_credentials(files["credentials"])
    credential_path = vpn.get("credentials_file")
    rules_path = vpn.get("rules_file")
    if not isinstance(credential_path, str) or (
        rules_path is not None and not isinstance(rules_path, str)
    ):
        raise DomainError("invalid_config_paths")
    protocols = vpn.get("listen_protocols", {})
    main_hosts = hosts.get("main_hosts", [])
    if not isinstance(protocols, dict) or not isinstance(main_hosts, list):
        raise DomainError("invalid_endpoint_config")
    names = []
    for host in main_hosts:
        if not isinstance(host, dict) or not isinstance(host.get("hostname"), str):
            raise DomainError("invalid_tls_host")
        names.append(host["hostname"])
    return {
        "version": version, "verified": capabilities(version).verified,
        "binary_path": exec_start[0], "working_directory": working_directory,
        "vpn_config_path": resolve_config_path(working_directory, exec_start[1]),
        "hosts_config_path": resolve_config_path(working_directory, exec_start[2]),
        "credentials_path": resolve_config_path(working_directory, credential_path),
        "rules_path": resolve_config_path(working_directory, rules_path) if rules_path else None,
        "listen_address": vpn.get("listen_address", "0.0.0.0:443"),
        "protocols": [p for p in ("http1", "http2", "quic") if p in protocols],
        "tls_hosts": names, "client_count": len(credentials),
        "hashes": {name: source_hash(value) for name, value in files.items()},
    }


@dataclass(frozen=True)
class Credential:
    username: str
    password: str = field(repr=False)
    enabled: bool = True
    expires_at: int | None = None
    max_http2_conns: int | None = None
    max_http3_conns: int | None = None


def validate_credential(value: Credential):
    for text in (value.username, value.password):
        if not isinstance(text, str) or not text or text.strip() != text or '"' in text or "\\" in text:
            raise DomainError("unsupported_credential_characters")
        if any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise DomainError("unsupported_credential_characters")
    for limit in (value.max_http2_conns, value.max_http3_conns):
        if limit is not None and (type(limit) is not int or not 0 <= limit <= 2**32 - 1):
            raise DomainError("invalid_connection_limit")


def parse_credentials(content: bytes) -> list[Credential]:
    document = parse_document(content)
    if set(document) != {"client"} or not isinstance(document["client"], tomlkit.items.AoT):
        raise DomainError("unsupported_credentials_structure")
    result = []
    known = {"username", "password", "max_http2_conns", "max_http3_conns"}
    for entry in document["client"]:
        if set(entry) - known or not {"username", "password"} <= set(entry):
            raise DomainError("unsupported_credential_fields")
        credential = Credential(**entry.unwrap())
        validate_credential(credential)
        result.append(credential)
    if len({c.username for c in result}) != len(result):
        raise DomainError("duplicate_username")
    return result


def render_credentials(credentials: list[Credential], now: int) -> bytes:
    if len({c.username for c in credentials}) != len(credentials):
        raise DomainError("duplicate_username")
    entries = tomlkit.aot()
    for value in credentials:
        validate_credential(value)
        if not value.enabled or (value.expires_at is not None and value.expires_at <= now):
            continue
        entry = tomlkit.table()
        entry["username"], entry["password"] = value.username, value.password
        for key in ("max_http2_conns", "max_http3_conns"):
            if getattr(value, key) is not None:
                entry[key] = getattr(value, key)
        entries.append(entry)
    if not entries:
        raise DomainError("empty_credentials_unsupported_v1_1_0")
    document = tomlkit.document()
    document["client"] = entries
    return tomlkit.dumps(document).encode()


def export_command(binary: str, vpn: str, hosts: str, username: str,
                   address: str, format: Literal["deeplink", "toml"], version: str) -> list[str]:
    if not capabilities(version).export_deeplink:
        raise DomainError("unsupported_endpoint_version")
    if format not in {"deeplink", "toml"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}", username):
        raise DomainError("invalid_export_request")
    if not re.fullmatch(r"[A-Za-z0-9.:[\]-]{1,253}", address) or address.startswith("-"):
        raise DomainError("invalid_public_address")
    # Registry-owned paths, not HTTP parameters. Arguments are never joined into shell text.
    return [binary, vpn, hosts, "-c", username, "-a", address, "--format", format]
