import json
import secrets
import threading
import time
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tunnelui.domain.errors import DomainError
from tunnelui.integrations.trusttunnel import capabilities, parse_credentials, preview
from tunnelui.models import Attachment, Client, Inbound
from tunnelui.security import SecretBox
from tunnelui.services.apply import SandboxApplyService
from tunnelui.services.audit import record
from tunnelui.services.operations import InboundLocks
from tunnelui.system.files import private_backup


class SandboxAdoptionService:
    """Offline adoption use case, intentionally not exposed as an HTTP/host operation.

    Service metadata is setup-owned. Confirmation accepts an opaque preview ID only.
    Native provider and durable coordination remain a production prerequisite.
    """

    def __init__(self, sandbox: SandboxApplyService, registry_id: str,
                 working_directory: str, exec_start: list[str], version: str,
                 public_address: str = "example.test:443", locks: InboundLocks | None = None):
        self.sandbox, self.registry_id = sandbox, registry_id
        self.working_directory, self.exec_start, self.version = working_directory, exec_start, version
        self.public_address = public_address
        self._pending: tuple[str, float, dict] | None = None
        self._lock = threading.Lock()
        self._inbound_locks = locks or InboundLocks()

    def _preview(self):
        return preview(self.working_directory, self.exec_start, self.sandbox.snapshot(), self.version)

    def detect(self):
        with self._lock:
            data = self._preview()
            token = secrets.token_urlsafe(24)
            self._pending = (token, time.monotonic() + 300, data)
            cap = capabilities(self.version)
            return {
                **data, "preview_id": token, "registry_id": self.registry_id,
                "service_name": self.registry_id,
                "public_address": self.public_address,
                "service_status": "running" if self.sandbox.provider.running else "stopped",
                "capabilities": {
                    "client_metrics": cap.client_metrics,
                    "export_deeplink": cap.export_deeplink,
                    "tls_reload": cap.tls_reload,
                    "credential_reload": cap.credential_reload,
                },
                "warnings": ([] if cap.verified else ["Версия endpoint не подтверждена"]),
            }

    def discover(self):
        data = self._preview()
        return {
            "found": True, "registry_id": self.registry_id,
            "service_name": self.registry_id, "version": data["version"],
            "working_directory": data["working_directory"],
            "listen_address": data["listen_address"],
            "service_status": "running" if self.sandbox.provider.running else "stopped",
        }

    def confirm(self, preview_id: str, db: Session, box: SecretBox, admin: str):
        with self._lock, self._inbound_locks.acquire(self.registry_id):
            pending = self._pending
            if not pending or pending[0] != preview_id or pending[1] <= time.monotonic():
                raise DomainError("adoption_preview_expired")
            snapshot = self.sandbox.snapshot()
            fresh = preview(self.working_directory, self.exec_start, snapshot, self.version)
            if fresh != pending[2]:
                raise DomainError("drift_conflict")
            if not fresh["verified"]:
                raise DomainError("unsupported_endpoint_version")
            if db.scalar(select(Inbound.id).where(Inbound.registry_id == self.registry_id)):
                raise DomainError("inbound_already_adopted")
            credentials = parse_credentials(snapshot["credentials"])
            names = [c.username for c in credentials]
            if db.scalar(select(Client.id).where(Client.username.in_(names)).limit(1)):
                raise DomainError("adoption_username_conflict")
            operation_id = str(uuid.uuid4())
            private_backup(self.sandbox.backup_root, operation_id, snapshot)
            inbound = Inbound(name="TrustTunnel", kind="trusttunnel", registry_id=self.registry_id,
                              metadata_json=json.dumps({k: v for k, v in fresh.items() if k != "hashes"}),
                              hashes_json=json.dumps(fresh["hashes"]),
                              public_address=self.public_address, config_state="synced")
            try:
                db.add(inbound)
                db.flush()
                for credential in credentials:
                    client = Client(username=credential.username, display_name=credential.username)
                    db.add(client)
                    db.flush()
                    db.add(Attachment(client_id=client.id, inbound_id=inbound.id,
                                      secret_ciphertext=box.encrypt(credential.password),
                                      max_http2_conns=credential.max_http2_conns,
                                      max_http3_conns=credential.max_http3_conns,
                                      enabled=True, desired_state="active",
                                      applied_state="active", sync_state="active"))
                record(db, admin, "inbound.adopt", "inbound", inbound.id)
                db.commit()
            except IntegrityError:
                db.rollback()
                raise DomainError("adoption_conflict") from None
            self._pending = None
            return {"inbound_id": inbound.id, "backup_id": operation_id, "client_count": len(credentials)}
