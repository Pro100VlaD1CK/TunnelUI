# Архитектура

## Границы и владение
TunnelUI владеет Client и его desired attachments. TrustTunnel managed inbound
представляет один endpoint service. 3x-ui остаётся владельцем своих transport,
security и subscription. Один Client может иметь несколько attachments; разные
passwords по inbound. Global enabled/expiry фильтруют credentials во время каждого apply.

## Слои
API валидирует запросы и авторизует; services координируют use cases; repositories
владеют SQL; domain не знает HTTP/systemd; integrations реализуют TrustTunnelProvider,
MetricsProvider, XUIProvider; system содержит SystemProvider и FakeSystemProvider.
Production provider не подменяется fake автоматически при ошибке.

## Privileged agent (проект, Phase 5)
Unix socket /run/tunnelui-agent/agent.sock, root-owned, group tunnelui, 0660,
SO_PEERCRED allowlisted backend UID. Versioned JSON protocol, request size/time bounds.
В запросах registry inbound ID, operation ID, expected hashes, typed config payload;
нет paths, command, environment или service names из HTTP. Root-owned registry
связывает ID с unit, canonical binary, working directory, file IDs и backup root.
Операции status, discover, preview, backup, apply, restart, health, bounded journal,
export-client. Reload только TLS при доказанной capability. Экспорт без mutating
--generate-client-random-prefix. stdout secret возвращается явно, stderr scrubbed.
Для apply validate typed data ещё раз; не выполнять команды от web. ExecStart
считывается структурированно через systemd D-Bus. Поддержать только прямой запуск
ожидаемого binary; wrapper/env/shell units — manual review, не эвристика.
Reject symlinks у managed config, unexpected hardlinks, writable parent, paths вне
allowlist; cert symlinks read-only отдельным путём (Let's Encrypt не ломать).
NoNewPrivileges для web, root agent минимальные permissions. Не давать web sudo.

## Adoption
Текущий sandbox реализует Detect → snapshot bytes/hashes → parse → secret-free preview
с TTL → explicit confirmation → повторное чтение и hash check → backup → DB metadata
и encrypted attachments. Collision username и неизвестные credential fields fail closed.
Ни initial files, ни unit не переписываются. HTTP не принимает filesystem paths.
Production discovery позднее заменит fixture registry на root-owned allowlist и
структурированный systemd D-Bus ответ.

## Apply и recovery
Sandbox coordinator использует per-inbound in-process lock и idempotency ID. Durable
DB states: pending, preparing, backed_up, writing, applying, checking, succeeded,
rolling_back, rolled_back, failed, needs_recovery. Checkpoints commit перед каждой
границей внешнего эффекта. Operation хранит только hashes и safe error codes; secret
bytes находятся только в зашифрованной БД и private backup.

Перед replace проверяются hashes всех managed files. После restart/health failure
восстанавливается operation-owned backup, затем повторяются restart и health. Если
это не подтверждает здоровье, inbound получает recovery_required, новые apply
блокируются, UI предлагает explicit recover. Desired/applied state изменяется только
после подтверждённого результата. AuditEvent фиксирует безопасный операторский итог
отдельно от технического Operation journal.

Production требует OS lock, crash reconciliation при startup, descriptor-based
filesystem boundary и helper-owned journal/backup. Текущий native filesystem core
работает только с explicit sandbox directories и FakeSystemProvider.

## Expiry и sync (следующие этапы)
UTC clock; durable scheduler периодически находит due attachments, группирует по
inbound, генерирует credentials и ставит apply operation. DB disabled не равно
effective disabled до successful apply. Последний active client v1.1.0 — блокируемый
edge case с видимой ошибкой. Quota не реализуется без persistent accounting.
3x-ui timeout → re-read remote by email/id → conflict or retry; не blindly create.
Delete/disable external с отдельным подтверждением и reconciliation state.

## UI
Hash routes для static serving. Query cache не хранит passwords. Login → Входящие.
Таблицы плотные, empty state объясняет следующий безопасный шаг. Sandbox service
status маркируется как тестовый контур; production status не симулируется. Profiles
экспортируется по явно выбранному inbound, поэтому несколько attachments не создают
неоднозначный QR. Partial apply ведёт прямо к журналу входящего. Ошибки остаются видимыми.
