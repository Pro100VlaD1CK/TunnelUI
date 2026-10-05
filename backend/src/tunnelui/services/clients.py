from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tunnelui.domain.clients import ClientInput, ClientUpdate
from tunnelui.domain.errors import DomainError
from tunnelui.models import Client, now
from tunnelui.repositories.clients import ClientRepository
from tunnelui.services.audit import record


class ClientService:
    def __init__(self, db: Session, admin: str):
        self.db, self.admin = db, admin
        self.repo = ClientRepository(db)

    def create(self, data: ClientInput):
        client = Client(**data.model_dump())
        self.db.add(client)
        try:
            self.db.flush()
            record(self.db, self.admin, "client.create", "client", client.id)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise DomainError("duplicate_username") from None
        return client

    def editable(self, client_id: str):
        client = self.db.get(Client, client_id)
        if not client:
            raise DomainError("client_not_found", 404)
        if self.repo.attached(client_id):
            raise DomainError("attached_client_requires_apply")
        return client

    def edit(self, client_id: str, data: ClientUpdate):
        client = self.db.get(Client, client_id)
        if not client:
            raise DomainError("client_not_found", 404)
        if self.repo.attached(client_id) and (
            data.username != client.username
            or data.enabled != client.enabled
            or data.expires_at != client.expires_at
        ):
            raise DomainError("attached_client_requires_apply")
        action = "client.disable" if client.enabled and not data.enabled else "client.edit"
        try:
            result = self.db.execute(update(Client).where(
                Client.id == client_id, Client.revision == data.revision
            ).values(**data.model_dump(exclude={"revision"}), updated_at=now(),
                     revision=data.revision + 1))
            if result.rowcount != 1:
                raise DomainError("revision_conflict")
            record(self.db, self.admin, action, "client", client_id)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise DomainError("duplicate_username") from None
        self.db.refresh(client)
        return client

    def remove(self, client_id: str, revision: int):
        self.editable(client_id)
        result = self.db.execute(delete(Client).where(
            Client.id == client_id, Client.revision == revision
        ))
        if result.rowcount != 1:
            raise DomainError("revision_conflict")
        record(self.db, self.admin, "client.delete", "client", client_id)
        self.db.commit()
