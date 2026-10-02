from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from tunnelui.models import Attachment, Client


class ClientRepository:
    def __init__(self, db: Session):
        self.db = db

    def page(self, query: str, enabled: bool | None, offset: int, limit: int):
        statement = select(Client)
        if query:
            pattern = f"%{query.replace('%', '/%').replace('_', '/_')}%"
            statement = statement.where(or_(
                Client.username.ilike(pattern, escape="/"),
                Client.display_name.ilike(pattern, escape="/"),
            ))
        if enabled is not None:
            statement = statement.where(Client.enabled == enabled)
        count = self.db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.db.scalars(statement.order_by(Client.username).offset(offset).limit(limit)).all()
        return rows, count

    def attached(self, client_id: str) -> bool:
        return self.db.scalar(select(Attachment.id).where(
            Attachment.client_id == client_id
        ).limit(1)) is not None
