# Архитектура

## Границы и владение
TunnelUI владеет Client и его desired attachments. TrustTunnel managed inbound
представляет один endpoint service. Другие VPN-системы вне scope. Один Client может иметь несколько attachments; разные
passwords по inbound. Global enabled фильтрует credentials; expiry фильтрует их
после явного перехода клиента к управляемой политике срока. TrustTunnel v1.1.0
не имеет отдельного серверного поля expiry.

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
`files.prepare_config`, `files.commit_config`, `files.restore_config`,
`files.cleanup_config` (только typed `vpn`/`rules`),
`service.status`, `service.restart`, allowlisted `service.reload`, `health.probe`,
`profile.export`, `managed.describe`. Агент сообщает allowlisted metadata для Linux
discovery/adoption; journal streaming и произвольный config apply не поддерживаются.
Reload по умолчанию запрещён; credentials требуют restart.

Для настроек и правил `ConfigurationService` читает фактический snapshot и допускает
только подтверждённую версию 1.1.0. Редактор vpn.toml ограничен семью полями:
`ipv6_available`, `allow_private_network_connections` и пятью timeout в секундах.
Listen/bind, TLS paths, listener protocols, forwarding и metrics listener не меняются.
Правила — только официальный упорядоченный `[[rule]]` с `cidr`,
`client_random_prefix`, `action`; неподдерживаемая структура блокирует запись.
tomlkit сохраняет не редактируемые поля и комментарии по возможности. Preview
связывает желаемое состояние с hashes всех managed files; apply повторно проверяет
их под process/agent locks, затем использует существующие backup/checkpoint/restart/
health/rollback/recovery. Новые agent IPC операции не принимают filesystem paths.

RuntimeMetricsService не применяет конфигурацию и не сохраняет наблюдения. Только
если подтверждённая версия и включён `per_client_metrics`, endpoint `/clients`
опрашивается по literal loopback IP из vpn.toml с timeout и лимитом ответа 2 MiB.
Отсутствие источника, запрет listener и нулевые значения различаются; счётчики
подписаны как относящиеся к текущему процессу endpoint, без quota/accounting.

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
Client/attachment actions выполняют preflight known drift и чужого pending до записи
desired state. `AttachmentIntent` и `ClientAccessIntent` фиксируют idempotency до apply;
fingerprint пароля — keyed HMAC, plaintext в intent нет. Повтор того же запроса после
потери ответа возвращает прежний Operation, не повторяя restart. Глобальный enabled
применяется последовательно к каждому inbound; общий результат может быть partial.
`ClientDeletion` сохраняет secret-free replay результата. Полное удаление клиента
сначала помечает все его attachments на отзыв, применяет credentials по inbound и
проверяет отсутствие username в каждом актуальном snapshot. DB client/attachments
удаляются только после подтверждения всех файлов/hashes; partial оставляет запись,
операции и явный путь восстановления. Это не общая транзакция нескольких служб.

Миграция 0004 добавляет applied username/encrypted password/expiry policy/fact of
expiry revocation к каждому attachment. Username/expiry изменяются через
`ClientCredentialService`: unique/CAS, preflight каждого inbound, keyed-HMAC
`CredentialIntent`, последовательные Operation и explicit partial outcome.
Rotation меняет только выбранный attachment; предыдущий encrypted password
сохраняется как applied до health/hash success или восстановления после rollback.
Profile export требует совпадения desired/applied credential и отсутствия drift.

`GET /inbounds/{id}/pending` показывает изменения, внешние файлы и влияние отмены
глобального enabled на другие inbounds. Fingerprint включает revisions и ожидаемые
hashes; `cancel-pending` повторно проверяет preview под process/agent locks и не
переписывает файлы. При отмене частично применённого глобального доступа другой
inbound может стать pending — оператор затем применяет его отдельно. Re-import
сохраняет запрет при pending/recovery; после известного drift без desired commit
re-import возможен сразу. При гонке после preflight pending остаётся видимым.

Один coordinator использует per-inbound in-process lock и idempotency ID; Linux
environment добавляет agent-owned OS lock. Durable
DB states: pending, preparing, backed_up, writing, applying, checking, succeeded,
rolling_back, rolled_back, failed, needs_recovery. Checkpoints commit перед каждой
границей внешнего эффекта. Operation хранит только hashes и safe error codes; secret
bytes находятся только в зашифрованной БД и private backup.

Credential/expiry операции и replacement-операции `vpn`/`rules` используют тот же
coordinator и одну блокировку inbound. Replacement допускает только typed file ID,
отказывает при pending credential changes и не обновляет applied credential state.
Expiry повторно проверяет deadline/revision внутри agent lock и отказывает при
активной settings/rules Operation. Аргументы `replacement`, `request_fingerprint`
и `expected_expiry` передаются именованно, чтобы расширение одного workflow не могло
изменить смысл другого позиционным сдвигом.

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

## Expiry и sync
`ExpirySetting.enabled=false` после миграции 0004, старые `expires_at` получают
`expiry_managed=false`. Настройки показывают fingerprinted preview до opt-in.
`tunnelui expiry-run` — разовый unprivileged CLI, вызываемый примерным systemd
timer. Он не создаёт web background thread: находит due клиентов по UTC,
фиксирует `ExpiryAttempt` с CAS lease и повторным cooldown, повторно проверяет
client revision/deadline внутри agent lock и вызывает прежний ApplyCoordinator.
Operation key детерминирован для попытки и переживает потерянный ответ. При отказе
состояние и safe code видны оператору; manual retry только планирует следующий
проход. Последний active credential v1.1.0 блокируется без dummy пользователя.
Фактический отзыв обозначает `Attachment.applied_expired`; `Client.expires_at`
сам по себе не доказывает, что доступ прекращён. Установка срока в будущем и
его продление проходят через apply-aware service. Quota не реализована без
persistent accounting. Timer и новый workflow требуют Linux CI/Debian staging.

## UI
Hash routes для static serving. Query cache не хранит passwords. Login → Входящие.
Таблицы плотные, empty state объясняет следующий безопасный шаг. Sandbox service
status маркируется как тестовый контур; production status не симулируется. Profiles
экспортируется по явно выбранному inbound, поэтому несколько attachments не создают
неоднозначный QR. Partial apply ведёт прямо к журналу входящего. Mobile Settings
использует компактный selector разделов, desktop сохраняет Ant Tabs. Ошибки видимы.
