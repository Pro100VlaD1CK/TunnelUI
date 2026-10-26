# TunnelUI

Панель управления экземплярами TrustTunnel на FastAPI + React + Ant Design. Главный документ:
[PROJECT.md](PROJECT.md). Продукт: [PRODUCT.md](PRODUCT.md), UI: [DESIGN.md](DESIGN.md).

Реализованы авторизация, клиенты, аудит, интерфейс управления и безопасный
adoption/apply workflow. Локальный sandbox использует fake provider. Linux backend
работает через отдельный root-owned агент с Unix socket и systemd D-Bus.
Локально реализованы safe username/expiry changes, password rotation выбранного
TrustTunnel-доступа и выключенный по умолчанию durable expiry worker с preview.
Миграция 0004 и новый timer ещё не проверены на Debian staging и не включались
на работающем сервере.
По сообщению владельца проекта, import/apply/rollback/export и VPN-подключение
проверялись вручную на отдельном Debian staging; текущий проход не повторяет
серверную проверку и не меняет production.

- [Локальный запуск и тесты](docs/DEPLOYMENT.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [Безопасность и production gates](docs/SECURITY.md)
- [Проверенные официальные версии](docs/RESEARCH.md)

Текущая разработка ведётся только локально. Прежде чем обновлять сервер, нужны
отдельные staging-проверки изменённых путей и явное разрешение на deployment.
