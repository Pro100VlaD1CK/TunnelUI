# TunnelUI
<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
По заданию: FastAPI, Python 3.11+, SQLAlchemy 2, Alembic, SQLite;
React, TypeScript, Vite, Ant Design, TanStack Query. Без Jinja UI.

## Users
Администратор собственного VPN-сервера; первая версия — один admin.
Он выдаёт, изменяет и отзывает доступ и проверяет фактическое состояние применения.

## Product Purpose
Единое управление клиентами TrustTunnel и внешних inbound 3x-ui с безопасным
изменением существующей установки. Успех — понятное эффективное состояние доступа
и возможность восстановить прежнюю конфигурацию после неуспешного apply.

## Operating Context
Уже работающий Debian 12 с TrustTunnel v1.1.0 и 3x-ui v3.8.5. Доступ к production
и deployment не разрешены текущей задачей. Desktop — основной рабочий контекст,
mobile — поддерживаемый web viewport. Обычный масштаб клиентов пока не подтверждён;
списки имеют server-side pagination, 25 строк по умолчанию.

## Capabilities and Constraints
Входящие → Клиенты → Профили → Правила → Настройки → Аудит. Dashboard отсутствует.
Один Client может иметь несколько inbound. TrustTunnel управляется напрямую через
ограниченный агент, Hysteria2 через 3x-ui API, Mihomo через upstream subscription.
Нельзя менять working services, открывать metrics наружу, показывать secrets в
списках, обещать quota или credentials hot reload. Initial adoption read-only до
подтверждения. Deferred функции явно обозначаются как не реализованные.

## Brand Commitments
Рабочее имя TunnelUI. Русский основной язык. Light/dark. UX-reference 3x-ui:
sidebar, плотные таблицы, быстрые действия, editing drawer/modal, status badges.
Не копировать branding, исходный frontend, CSS или layout пиксель-в-пиксель.
Не marketing SaaS: без gradients, oversized headings, nested cards и декоративной animation.

## Evidence on Hand
Подробный brief пользователя, version-pinned official sources в docs/RESEARCH.md.
Live server metrics и live service status не получены. Не фабриковать данные.

## Product Principles
- Плотность информации вместе с читаемостью.
- Быстрая операция с понятным последствием и подтверждением опасного действия.
- Видимые drift, ошибки, pending и rollback, без silent partial apply.
- Секреты доступны только по явному действию.

## Accessibility & Inclusion
Клавиатурная навигация, текст вместе со статусным цветом, именованные icon buttons,
focus-visible, reduced motion и читаемость в обеих темах. WCAG AA — инженерная цель,
не заявление о выполненной сертификации.
