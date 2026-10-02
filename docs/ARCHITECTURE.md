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
Detect only allowlisted services → snapshot bytes + hashes + version/help + status
→ parse preserving unknown vpn/hosts/rules → secret-free preview с snapshot ID/TTL
→ explicit confirmation → recheck hashes под lock → backup → DB metadata и encrypted
credentials transaction. Collision username требует explicit mapping, не auto-merge.
Ни initial files, ни unit не переписываются. Preview нельзя подменить path из HTTP.

## Apply и recovery
Per-inbound OS lock и операция с idempotency ID. States: pending, validating,
backed_up, files_written, restarting, healthy, applied, rolling_back, rolled_back,
rollback_failed, interrupted. Журнал хранится агентом с fsync, secrets только в
private config backup, не в journal. На startup незавершённые операции блокируют
новые apply до reconciliation. Проверка hashes всех четырёх файлов, не только
изменяемого. После сбоя health вернуть старые bytes, metadata и service; rollback
failure виден как degraded, никогда success. Native filesystem core текущего
прохода только sandbox; production флаг пока отсутствует.

## Expiry и sync (следующие этапы)
UTC clock; durable scheduler периодически находит due attachments, группирует по
inbound, генерирует credentials и ставит apply operation. DB disabled не равно
effective disabled до successful apply. Последний active client v1.1.0 — блокируемый
edge case с видимой ошибкой. Quota не реализуется без persistent accounting.
3x-ui timeout → re-read remote by email/id → conflict or retry; не blindly create.
Delete/disable external с отдельным подтверждением и reconciliation state.

## UI
Hash routes для static serving. Query cache не хранит passwords. Login → Входящие.
Таблицы плотные, empty state объясняет следующий безопасный шаг. Нет mock health
или fictitious data. Deferred action disabled с причиной. Ошибки остаются видимыми.
