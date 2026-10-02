from sqlalchemy.orm import Session

from tunnelui.models import AuditEvent

# No caller-supplied details or upstream error messages are accepted.
SUMMARIES = {
    "client.create": "Создан глобальный клиент",
    "client.edit": "Обновлена запись клиента",
    "client.disable": "Отключена запись клиента",
    "client.delete": "Удалён клиент без привязок",
    "auth.login": "Выполнен вход администратора",
    "auth.logout": "Сессия завершена",
    "inbound.adopt": "Импортирована конфигурация входящего без изменения файлов",
}


def record(db: Session, admin: str, action: str, entity_type: str, entity_id: str):
    db.add(AuditEvent(admin=admin, action=action, entity_type=entity_type,
                      entity_id=entity_id, result="success", summary=SUMMARIES[action]))
