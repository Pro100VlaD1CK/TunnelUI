from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from tunnelui.domain.errors import DomainError


class ExternalInbound(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: int
    remark: str
    protocol: str
    port: int
    enable: bool


class XUIReadOnlyAdapter:
    """v3.8.5 contract spike. No remote mutations; base includes configured web path.

    Transport injected for offline tests. No redirects, environment proxy or TLS bypass.
    Raw response/settings/credentials are never returned to list API.
    """

    def __init__(self, base_url: str, token: str, *, transport=None):
        url = urlsplit(base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise DomainError("invalid_xui_base_url")
        self._http = httpx.Client(base_url=base_url.rstrip("/") + "/",
                                  headers={"Authorization": f"Bearer {token}"},
                                  timeout=5, verify=True, follow_redirects=False,
                                  trust_env=False, transport=transport)

    def close(self):
        self._http.close()

    def _get(self, path: str):
        try:
            with self._http.stream("GET", path) as response:
                if response.status_code != 200:
                    raise DomainError("xui_http_error", 502)
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 2 * 1024 * 1024:
                        raise DomainError("xui_response_too_large", 502)
        except httpx.HTTPError:
            raise DomainError("xui_unreachable", 502) from None
        import json

        try:
            value = json.loads(payload)
        except (ValueError, UnicodeError):
            raise DomainError("invalid_xui_response", 502) from None
        if not isinstance(value, dict) or value.get("success") is not True or "obj" not in value:
            raise DomainError("xui_operation_failed", 502)
        return value["obj"]

    def list_inbounds(self) -> list[ExternalInbound]:
        value = self._get("panel/api/inbounds/list")
        if not isinstance(value, list):
            raise DomainError("invalid_xui_response", 502)
        try:
            return [ExternalInbound.model_validate(row) for row in value]
        except ValidationError:
            raise DomainError("invalid_xui_response", 502) from None

    def find_client(self, username: str) -> dict | None:
        value = self._get(f"panel/api/clients/get/{quote(username, safe='')}")
        if value is not None and not isinstance(value, dict):
            raise DomainError("invalid_xui_response", 502)
        return value


def sync_decision(username: str, remote: dict | None, expected_remote_id: int | None) -> str:
    """Plan only. Uncertain transport failures never map to 'missing' or automatic create."""
    if remote is None:
        return "conflict" if expected_remote_id is not None else "pending"
    if remote.get("email") != username or type(remote.get("id")) is not int:
        return "conflict"
    if expected_remote_id is None or remote["id"] != expected_remote_id:
        return "conflict"  # existing identity requires explicit binding approval
    return "synced"
