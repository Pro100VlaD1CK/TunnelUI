import json
import secrets
import threading
import time
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tunnelui.agent.client import AgentClientError, LinuxAgentClient
from tunnelui.domain.errors import DomainError
from tunnelui.integrations.trusttunnel import capabilities, parse_credentials, preview
from tunnelui.models import Attachment, Client, Inbound
from tunnelui.security import SecretBox
from tunnelui.services.audit import record
from tunnelui.services.operations import InboundLocks
from tunnelui.system.linux import LinuxManagedEnvironment
from tunnelui.system.provider import SystemOperationError


class LinuxAdoptionService:
    """Adopt one root-agent allowlisted TrustTunnel instance."""

    def __init__(
        self,
        client: LinuxAgentClient,
        environment: LinuxManagedEnvironment,
        locks: InboundLocks,
    ):
        self.client = client
        self.environment = environment
        self.registry_id = environment.registry_id
        self._inbound_locks = locks
        self._pending = None
        self._lock = threading.Lock()

    def _metadata(self) -> dict[str, object]:
        try:
            data = self.client.describe(self.registry_id)
        except AgentClientError:
            raise DomainError("management_unavailable", 503) from None

        required = (
            "service",
            "working_directory",
            "binary_path",
            "vpn_config_path",
            "hosts_config_path",
            "credentials_path",
            "rules_path",
            "public_address",
            "expected_version",
        )
        if any(not isinstance(data.get(key), str) for key in required):
            raise DomainError("managed_metadata_invalid", 503)

        if (
            data["expected_version"] != self.environment.version
            or data["public_address"] != self.environment.public_address
        ):
            raise DomainError("managed_metadata_mismatch", 503)

        return data

    def _evidence(self):
        metadata = self._metadata()

        try:
            snapshot = self.environment.snapshot()
        except OSError:
            raise DomainError("management_unavailable", 503) from None

        data = preview(
            metadata["working_directory"],
            [
                metadata["binary_path"],
                metadata["vpn_config_path"],
                metadata["hosts_config_path"],
            ],
            snapshot,
            metadata["expected_version"],
        )

        if (
            data["binary_path"] != metadata["binary_path"]
            or data["vpn_config_path"] != metadata["vpn_config_path"]
            or data["hosts_config_path"] != metadata["hosts_config_path"]
            or data["credentials_path"] != metadata["credentials_path"]
            or (
                data["rules_path"] is not None
                and data["rules_path"] != metadata["rules_path"]
            )
        ):
            raise DomainError("managed_metadata_mismatch", 503)

        evidence = {
            "preview": data,
            "service_name": metadata["service"],
            "public_address": metadata["public_address"],
        }
        return metadata, snapshot, data, evidence

    def _service_status(self) -> str:
        try:
            status = self.environment.provider.status()
        except SystemOperationError:
            return "unknown"
        return "running" if status.get("running") is True else "stopped"

    def discover(self):
        metadata, _snapshot, data, _evidence = self._evidence()
        return {
            "found": True,
            "registry_id": self.registry_id,
            "service_name": metadata["service"],
            "version": data["version"],
            "working_directory": data["working_directory"],
            "listen_address": data["listen_address"],
            "service_status": self._service_status(),
        }

    def detect(self):
        with self._lock:
            metadata, _snapshot, data, evidence = self._evidence()
            token = secrets.token_urlsafe(24)
            self._pending = (token, time.monotonic() + 300, evidence)

            cap = capabilities(data["version"])

            return {
                **data,
                "preview_id": token,
                "registry_id": self.registry_id,
                "service_name": metadata["service"],
                "public_address": metadata["public_address"],
                "service_status": self._service_status(),
                "capabilities": {
                    "client_metrics": cap.client_metrics,
                    "export_deeplink": cap.export_deeplink,
                    "tls_reload": cap.tls_reload,
                    "credential_reload": cap.credential_reload,
                },
                "warnings": (
                    [] if cap.verified else ["Версия endpoint не подтверждена"]
                ),
            }

    def confirm(
        self,
        preview_id: str,
        db: Session,
        box: SecretBox,
        admin: str,
    ):
        with self._lock, self._inbound_locks.acquire(self.registry_id):
            pending = self._pending
            if (
                not pending
                or pending[0] != preview_id
                or pending[1] <= time.monotonic()
            ):
                raise DomainError("adoption_preview_expired")

            operation_id = str(uuid.uuid4())

            try:
                with self.environment.operation(operation_id):
                    _metadata, snapshot, fresh, evidence = self._evidence()

                    if evidence != pending[2]:
                        raise DomainError("drift_conflict")

                    if not fresh["verified"]:
                        raise DomainError("unsupported_endpoint_version")

                    if db.scalar(
                        select(Inbound.id).where(
                            Inbound.registry_id == self.registry_id
                        )
                    ):
                        raise DomainError("inbound_already_adopted")

                    credentials = parse_credentials(snapshot["credentials"])
                    names = [item.username for item in credentials]

                    if names and db.scalar(
                        select(Client.id)
                        .where(Client.username.in_(names))
                        .limit(1)
                    ):
                        raise DomainError("adoption_username_conflict")

                    self.environment.create_backup(operation_id, snapshot)

                    inbound = Inbound(
                        name="TrustTunnel",
                        kind="trusttunnel",
                        registry_id=self.registry_id,
                        metadata_json=json.dumps(
                            {
                                key: value
                                for key, value in fresh.items()
                                if key != "hashes"
                            }
                        ),
                        hashes_json=json.dumps(fresh["hashes"]),
                        public_address=self.environment.public_address,
                        config_state="synced",
                    )

                    try:
                        db.add(inbound)
                        db.flush()

                        for credential in credentials:
                            client = Client(
                                username=credential.username,
                                display_name=credential.username,
                            )
                            db.add(client)
                            db.flush()

                            db.add(
                                Attachment(
                                    client_id=client.id,
                                    inbound_id=inbound.id,
                                    secret_ciphertext=box.encrypt(
                                        credential.password
                                    ),
                                    max_http2_conns=credential.max_http2_conns,
                                    max_http3_conns=credential.max_http3_conns,
                                    enabled=True,
                                    desired_state="active",
                                    applied_state="active",
                                    sync_state="active",
                                )
                            )

                        record(
                            db,
                            admin,
                            "inbound.adopt",
                            "inbound",
                            inbound.id,
                        )
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        raise DomainError("adoption_conflict") from None

            except OSError:
                raise DomainError("management_unavailable", 503) from None

            self._pending = None

            return {
                "inbound_id": inbound.id,
                "backup_id": operation_id,
                "client_count": len(credentials),
            }
