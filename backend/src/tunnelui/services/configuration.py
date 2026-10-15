"""Version-pinned, allowlisted vpn.toml and rules.toml editing."""

import hashlib
import json
from copy import deepcopy

import tomlkit
from sqlalchemy import select

from tunnelui.domain.errors import DomainError
from tunnelui.domain.rules import Rule
from tunnelui.integrations.trusttunnel import capabilities, parse_document, source_hash
from tunnelui.models import Inbound, Operation
from tunnelui.services.coordinator import TrustTunnelCoordinator
from tunnelui.services.operations import active_operation, hashes, operation_output

# Conservative editing subset of official v1.1.0 settings. Network binding,
# listener protocols, certificate paths and forwarding remain read-only.
EDITABLE: dict[str, tuple[type, int | bool, int | None, int | None]] = {
    "ipv6_available": (bool, True, None, None),
    "allow_private_network_connections": (bool, False, None, None),
    "tls_handshake_timeout_secs": (int, 10, 1, 300),
    "client_listener_timeout_secs": (int, 600, 1, 86400),
    "connection_establishment_timeout_secs": (int, 30, 1, 300),
    "tcp_connections_timeout_secs": (int, 604800, 1, 2592000),
    "udp_connections_timeout_secs": (int, 300, 1, 3600),
}
MAX_RULES = 200


def _values(document) -> dict[str, bool | int]:
    values = document.unwrap()
    result = {}
    for key, (expected_type, default, _minimum, _maximum) in EDITABLE.items():
        value = values.get(key, default)
        if type(value) is not expected_type:
            raise DomainError("unsupported_config_value")
        result[key] = value
    return result


def _validate_changes(changes: dict[str, object]) -> None:
    if not changes or set(changes) - set(EDITABLE):
        raise DomainError("unsupported_config_field")
    for key, value in changes.items():
        expected_type, _default, minimum, maximum = EDITABLE[key]
        if type(value) is not expected_type or (
            minimum is not None and not minimum <= value <= maximum
        ):
            raise DomainError("invalid_config_value")


def _rules(document) -> list[Rule]:
    values = document.unwrap()
    if set(values) - {"rule"}:
        raise DomainError("unsupported_rules_structure")
    entries = values.get("rule", [])
    if not isinstance(entries, list) or len(entries) > MAX_RULES:
        raise DomainError("unsupported_rules_structure")
    try:
        return [Rule.model_validate(entry) for entry in entries]
    except ValueError:
        raise DomainError("invalid_rules") from None


def _render_rules(document, desired: list[Rule]) -> bytes:
    old = list(document.get("rule", []))
    available = list(range(len(old)))
    entries = tomlkit.aot()
    for index, rule in enumerate(desired):
        values = rule.model_dump(exclude_none=True)
        exact = next((slot for slot in available if old[slot].unwrap() == values), None)
        slot = exact if exact is not None else (index if index in available else None)
        if slot is not None:
            available.remove(slot)
            entry = deepcopy(old[slot])
            for key in list(entry):
                if key not in values:
                    del entry[key]
            for key, value in values.items():
                entry[key] = value
        else:
            entry = tomlkit.table()
            for key, value in values.items():
                entry[key] = value
        entries.append(entry)
    document["rule"] = entries
    content = tomlkit.dumps(document).encode()
    return content or b"# No TrustTunnel filtering rules\n"


class ConfigurationService:
    def __init__(self, coordinator: TrustTunnelCoordinator, admin: str):
        self.coordinator, self.admin = coordinator, admin

    def _snapshot(self, inbound_id: str) -> tuple[Inbound, dict[str, bytes], bool]:
        with self.coordinator.sessions() as db:
            inbound = db.get(Inbound, inbound_id)
            if not inbound or inbound.kind != "trusttunnel":
                raise DomainError("inbound_not_found", 404)
            if not inbound.enabled:
                raise DomainError("inbound_not_managed")
            environment = self.coordinator.environment(inbound)
            metadata_version = json.loads(inbound.metadata_json or "{}").get("version", "")
            if (metadata_version != environment.version
                    or not capabilities(metadata_version).verified):
                raise DomainError("unsupported_endpoint_version")
            if inbound.config_state == "recovery_required":
                raise DomainError("recovery_required")
            if active_operation(db, inbound_id):
                raise DomainError("operation_in_progress")
            try:
                snapshot = environment.snapshot()
            except OSError:
                raise DomainError("management_unavailable", 503) from None
            drift = {key: source_hash(value) for key, value in snapshot.items()} != hashes(
                inbound.hashes_json
            )
            return inbound, snapshot, drift

    @staticmethod
    def _fingerprint(inbound_id: str, file_id: str, snapshot: dict[str, bytes],
                     desired: object) -> str:
        payload = {
            "inbound_id": inbound_id, "file_id": file_id,
            "hashes": {key: source_hash(value) for key, value in snapshot.items()},
            "desired": desired,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _request_fingerprint(inbound_id: str, file_id: str, key: str,
                             preview_fingerprint: str, desired: object) -> str:
        return hashlib.sha256(json.dumps({
            "inbound_id": inbound_id, "file_id": file_id, "key": key,
            "preview": preview_fingerprint, "desired": desired,
        }, sort_keys=True).encode()).hexdigest()

    def settings(self, inbound_id: str) -> dict:
        inbound, snapshot, drift = self._snapshot(inbound_id)
        vpn = parse_document(snapshot["vpn"])
        values = vpn.unwrap()
        return {
            "inbound_id": inbound.id, "version": json.loads(inbound.metadata_json)["version"],
            "listen_address": values.get("listen_address", "0.0.0.0:443"),
            "protocols": sorted(values.get("listen_protocols", {})),
            "values": _values(vpn),
            "configured": sorted(key for key in EDITABLE if key in values),
            "drift": drift, "restart_required": True,
        }

    def preview_settings(self, inbound_id: str, changes: dict[str, object]) -> tuple[dict, bytes]:
        _validate_changes(changes)
        _inbound, snapshot, drift = self._snapshot(inbound_id)
        if drift:
            raise DomainError("drift_conflict")
        vpn = parse_document(snapshot["vpn"])
        current = _values(vpn)
        changed = {key: value for key, value in changes.items() if current[key] != value}
        if not changed:
            raise DomainError("no_config_changes")
        for key, value in changed.items():
            vpn[key] = value
        return {
            "fingerprint": self._fingerprint(inbound_id, "vpn", snapshot, changes),
            "changed_files": ["vpn"], "changes": [{
                "field": key, "before": current[key], "after": value,
            } for key, value in changed.items()],
            "restart_required": True,
        }, tomlkit.dumps(vpn).encode()

    def rules(self, inbound_id: str) -> dict:
        _inbound, snapshot, drift = self._snapshot(inbound_id)
        parsed = _rules(parse_document(snapshot["rules"]))
        return {"inbound_id": inbound_id, "rules": [rule.model_dump() for rule in parsed],
                "drift": drift, "restart_required": True}

    def preview_rules(self, inbound_id: str, proposed: list[Rule]) -> tuple[dict, bytes]:
        if len(proposed) > MAX_RULES:
            raise DomainError("too_many_rules")
        _inbound, snapshot, drift = self._snapshot(inbound_id)
        if drift:
            raise DomainError("drift_conflict")
        document = parse_document(snapshot["rules"])
        old = _rules(document)
        if old == proposed:
            raise DomainError("no_config_changes")
        content = _render_rules(document, proposed)
        desired = [rule.model_dump(exclude_none=True) for rule in proposed]
        return {
            "fingerprint": self._fingerprint(inbound_id, "rules", snapshot, desired),
            "changed_files": ["rules"], "before_count": len(old),
            "after_count": len(proposed), "restart_required": True,
        }, content

    def apply(self, inbound_id: str, file_id: str, desired: object,
              fingerprint: str, key: str) -> dict:
        if file_id not in {"vpn", "rules"}:
            raise DomainError("invalid_file_id")
        request_fingerprint = self._request_fingerprint(
            inbound_id, file_id, key, fingerprint, desired
        )
        with self.coordinator.sessions() as db:
            existing = db.scalar(select(Operation).where(Operation.idempotency_key == key))
            if existing:
                if existing.request_fingerprint != request_fingerprint:
                    raise DomainError("idempotency_conflict")
                return operation_output(existing)
        if file_id == "vpn":
            view, content = self.preview_settings(inbound_id, desired)
            kind = "settings_apply"
        else:
            rules = [Rule.model_validate(item) for item in desired]
            view, content = self.preview_rules(inbound_id, rules)
            kind = "rules_apply"
        if view["fingerprint"] != fingerprint:
            raise DomainError("config_preview_changed")
        return self.coordinator.apply(
            inbound_id, key, self.admin, kind, (file_id, content), request_fingerprint
        )
