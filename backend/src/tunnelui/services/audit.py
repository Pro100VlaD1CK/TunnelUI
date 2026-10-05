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
    "attachment.create": "Клиент привязан к входящему",
    "attachment.update": "Изменено состояние доступа к входящему",
    "attachment.detach": "Клиент отвязан от входящего",
    "config.apply": "Конфигурация применена",
    "config.rollback": "Выполнен откат конфигурации",
    "inbound.reimport": "Внешнее состояние входящего импортировано повторно",
    "inbound.unmanage": "Входящее удалено из управления без удаления файлов",
    "service.restart": "Сервис перезапущен в sandbox",
}


def record(db: Session, admin: str, action: str, entity_type: str, entity_id: str):
    db.add(AuditEvent(admin=admin, action=action, entity_type=entity_type,
                      entity_id=entity_id, result="success", summary=SUMMARIES[action]))


def record_result(db: Session, admin: str, action: str, entity_type: str,
                  entity_id: str, result: str, summary: str | None = None):
    """Record only allowlisted summaries; callers cannot persist upstream details."""
    safe = summary if summary in {
        "Конфигурация применена, сервис отвечает",
        "Не удалось применить конфигурацию",
        "Предыдущая конфигурация восстановлена",
        "Откат не завершён; требуется восстановление",
    } else SUMMARIES[action]
    db.add(AuditEvent(admin=admin, action=action, entity_type=entity_type,
                      entity_id=entity_id, result=result, summary=safe))
