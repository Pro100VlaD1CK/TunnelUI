# Архитектура

## Границы и владение
TunnelUI владеет Client и его desired attachments. TrustTunnel managed inbound
представляет один endpoint service. Другие VPN-системы вне scope. Один Client может иметь несколько attachments; разные
passwords по inbound. Global enabled/expiry фильтруют credentials во время каждого apply.

## Слои
API валидирует запросы и авторизует; services координируют use cases; repositories
владеют SQL; domain не знает HTTP/systemd; integrations реализуют TrustTunnelProvider
и MetricsProvider; system содержит LinuxSystemProvider и FakeSystemProvider.
Production provider не подменяется fake автоматически при ошибке.

## Linux privileged boundary
`LinuxRuntime` встраивает `LinuxManagedEnvironment` и `LinuxSystemProvider` в тот же
`TrustTunnelCoordinator`, что использует sandbox. Web остаётся без root и передаёт
только managed ID/operation ID через Unix socket `/run/tunnelui/agent.sock`.
`tunnelui-agent` запускается отдельным root unit через systemd socket activation;
SO_PEERCRED допускает только UID `backend_user` из root-owned registry. Протокол —
versioned JSON с typed arguments, request ID, лимитами 6 MiB запроса / 24 MiB ответа,
таймаутом чтения 5 с и bounded client read. Нет произвольных paths, commands,
environment или unit names из HTTP. Request ID связывает ответ с запросом, но не
является replay cache; idempotency внешней mutation принадлежит coordinator.

Root-owned `/etc/tunnelui/agent.toml` связывает ID с unit, binary, working directory,
четырьмя file IDs, backup root, health target и ожидаемой версией. Registry читается
через descriptor walk с nofollow, проверкой владельца/режима/размера; лишние поля,
повторы unit/files и некорректные типы отвергаются. Агент поддерживает `lock.acquire`,
`lock.release`, `files.snapshot`, `files.backup`, `files.prepare_credentials`,
`files.commit_credentials`, `files.restore_credentials`, `files.cleanup`,
`service.status`, `service.restart`, allowlisted `service.reload`, `health.probe`,
`profile.export`, `managed.describe`. Агент сообщает allowlisted metadata для Linux
discovery/adoption; journal streaming и произвольный config apply не поддерживаются.
Reload по умолчанию запрещён; credentials требуют restart.

Файловый adapter проходит директории через `openat`/`O_NOFOLLOW`, проверяет owner,
mode, regular file и link count, ограничивает файл 4 MiB, пишет same-directory temp,
fsync и atomic rename; backup хранится в private root-owned каталоге. `flock`
сериализует inbound между процессами, дополнительно резервируется peer PID;
исчезнувший peer допускает reclaim. Systemd status/restart/reload используют D-Bus,
включая bounded JobRemoved wait; shell/sudo не используются. Официальный CLI v1.1.0
запускается из проверенного binary descriptor, с фиксированными argv, чистым env,
bounded stdout (64 KiB)/timeout и скрытым stderr. Сертификаты остаются read-only;
Let's Encrypt symlinks не переписываются. `NoNewPrivileges` применяется к web unit.

## Adoption
Текущий sandbox реализует Detect → snapshot bytes/hashes → parse → secret-free preview
с TTL → explicit confirmation → повторное чтение и hash check → backup → DB metadata
и encrypted attachments. Collision username и неизвестные credential fields fail closed.
Ни initial files, ни unit не переписываются. HTTP не принимает filesystem paths.
Linux adoption использует `managed.describe` и snapshot от агента, повторно проверяет
их перед confirm и импортирует metadata, hashes, клиентов и encrypted credentials без
записи исходных конфигов или restart. Повтор confirm того же preview идемпотентен в
живом процессе; после restart при неопределённом ответе нужно сверить список inbounds.
Изменения внешних файлов обнаруживаются через hashes. Явный re-import требует точного
совпадения username, отсутствия pending изменений и recovery; обновляет credentials
и metadata из одного snapshot, public address — из root registry. Bind address из
vpn.toml и публичный адрес хранятся раздельно. Реальный systemd ExecStart пока не
сверяется с registry автоматически, поэтому совместимость unit остаётся проверкой
оператора на staging.

## Apply и recovery
Один coordinator использует per-inbound in-process lock и idempotency ID; Linux
environment добавляет agent-owned OS lock. Durable
DB states: pending, preparing, backed_up, writing, applying, checking, succeeded,
rolling_back, rolled_back, failed, needs_recovery. Checkpoints commit перед каждой
границей внешнего эффекта. Operation хранит только hashes и safe error codes; secret
bytes находятся только в зашифрованной БД и private backup.

Перед replace coordinator проверяет hashes всех managed files после prepare;
agent повторяет сравнение с ожидаемыми hashes прямо перед rename. При явном
`drift_conflict` внешний файл сохраняется и rollback не запускается. Внешний
редактор, не использующий lock TunnelUI, всё ещё может попасть в короткий интервал
между сравнением и rename; это остаётся ограничением для staging acceptance.
После restart/health failure rollback сверяет текущий snapshot с исходным и
записанным hash и не восстанавливает backup поверх неизвестной внешней правки.
При расхождении остаётся `needs_recovery` для оператора. Иначе
восстанавливается operation-owned backup, затем повторяются restart и health;
перед отметкой об успехе hashes сверяются снова. Если
это не подтверждает здоровье, inbound получает recovery_required, новые apply
блокируются, UI предлагает explicit recover. Desired/applied state изменяется только
после подтверждённого результата. AuditEvent фиксирует безопасный операторский итог
отдельно от технического Operation journal.

При потерянном ответе после rename coordinator считает запись совершённой и
пытается rollback. Temp после crash удаляется перед restore. Operation journal
остаётся в SQLite, а backup — у агента; стартовое `recover_all()` и ручной recover
используют тот же workflow. Протокол не хранит secret payload. Неопределённые
результаты D-Bus, health или filesystem требуют повторной Debian staging проверки
после изменений. По сообщению владельца проекта, прежняя версия Linux adoption уже
прошла ручную проверку на отдельном Debian staging, включая apply/rollback/export/VPN.

## Expiry и sync (следующие этапы)
UTC clock; durable scheduler периодически находит due attachments, группирует по
inbound, генерирует credentials и ставит apply operation. DB disabled не равно
effective disabled до successful apply. Последний active client v1.1.0 — блокируемый
edge case с видимой ошибкой. Quota не реализуется без persistent accounting.

## UI
Hash routes для static serving. Query cache не хранит passwords. Login → Входящие.
Таблицы плотные, empty state объясняет следующий безопасный шаг. Sandbox service
status маркируется как тестовый контур; production status не симулируется. Profiles
экспортируется по явно выбранному inbound, поэтому несколько attachments не создают
неоднозначный QR. Partial apply ведёт прямо к журналу входящего. Ошибки остаются видимыми.
