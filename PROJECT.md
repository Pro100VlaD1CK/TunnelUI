# TunnelUI

Главный технический документ. Состояние на 2026-10-27. Product context — `PRODUCT.md`,
визуальная система — `DESIGN.md`. В этом проходе Codex работает только с локальными
исходниками; приведённые ниже Debian staging результаты сообщил владелец проекта.

## Цель проекта
Независимая русскоязычная web-панель управления исключительно TrustTunnel:
несколько экземпляров, клиенты, профили, конфигурации, состояние и безопасный apply.
3x-ui остаётся только UX-референсом; Xray/Hysteria2/Mihomo/Clash вне scope.
Первая страница — «Входящие»; dashboard и необоснованная traffic quota не нужны.

## Текущая архитектура
Модульный монолит FastAPI / Pydantic 2 / SQLAlchemy 2 / Alembic / SQLite и
React / TypeScript / Vite / Ant Design / TanStack Query. Backend не должен работать
от root. API вызывает services, те используют repositories/domain и узкие provider
interfaces. DB, файлы и сервис не образуют общей транзакции, поэтому состояние
разделено на desired/applied/sync и фиксируется durable `Operation` journal.

Phase 2 sandbox composition сохраняет `FakeSystemProvider`, sandbox-файлы и fake
exporter. Новый Linux boundary встраивает `LinuxManagedEnvironment` за тем же
coordinator и вызывает отдельный root-owned `tunnelui-agent` по Unix socket только
при полной explicit agent configuration. Без неё production-default management
fail closed с 503; скрытого fake fallback нет. Linux adoption уже есть в коде;
прошлая версия вручную проверялась на отдельном Debian staging, а изменения этого
прохода требуют повторной проверки. Подробности — `docs/ARCHITECTURE.md`.

## Компоненты
- `backend/src/tunnelui`: API, domain, services, repositories, integrations, system adapters.
- `backend/migrations`: Alembic 0001–0004; схема не создаётся через `create_all`.
- `backend/tests`: temp directories, fakes и mocks; реальный systemd не вызывается.
- `frontend/src`: app shell, shared API/types и features auth/inbounds/clients/profiles/audit/settings/rules.
- `frontend/e2e`: Chromium-пути Phase 1/2, recovery, mobile и accessibility smoke checks.
- `.agents/skills/impeccable`: project-local design/UX skill; Ant Design остаётся библиотекой компонентов.
- `backend/src/tunnelui/agent`: versioned IPC, registry, OS lock, безопасные файлы,
  systemd D-Bus, health, официальный CLI exporter и socket server.
- `packaging`: примерные systemd/socket units, tmpfiles, registry и env; фактическую
  конфигурацию staging нужно сверять отдельно, примеры не считать её копией.

## Модель данных
`Admin`, `AdminSession`, `Client`, `Inbound`, `Attachment`, `Operation`, `AuditEvent`,
`AttachmentIntent`, `ClientAccessIntent`, `ClientDeletion`, `CredentialIntent`,
`ExpirySetting`, `ExpiryAttempt`.
Client глобален; credential и connection limits принадлежат attachment. Attachment
хранит encrypted password, desired/applied/sync state и revision. Operation хранит
kind, state, idempotency key, request fingerprint, ожидаемые/результирующие hashes,
safe error code и timestamps. В Operation нет password, config plaintext, tt://,
API token или private key. AuditEvent — отдельная сущность с allowlisted summary.

Миграция 0002 добавляет operation journal и attachment state. Operation states:
`pending → preparing → backed_up → writing → applying → checking → succeeded`;
ошибка после записи проходит `rolling_back → rolled_back`, а неподтверждённый rollback
заканчивается `needs_recovery`. До записи возможен `failed`.
Миграция 0003 добавляет secret-free idempotency intents, replay удалений и снимок
фактически применённых HTTP/2/3 limits и глобального enabled для каждого attachment.
Intents не содержат credential, plaintext config или deep-link.
Миграция 0004 сохраняет для каждого attachment подтверждённые username,
зашифрованный password, expiry policy и факт отзыва по сроку. Старые сроки
получают `expiry_managed=false`, scheduler — `enabled=false`; миграция не меняет
файлы и не перезапускает TrustTunnel. `CredentialIntent` хранит keyed HMAC
запроса и IDs; `ExpiryAttempt` — срок, попытку, cooldown и safe error.

## TrustTunnel integration
Проверен официальный tag v1.1.0, commit `fab5b8353a19332f935fa30869307d37d4a898d1`.
Sandbox и Linux workflow: Detect → Parse → Preview → Confirm adoption → backup → сохранить
metadata/hashes и зашифрованные attachments. Adoption не переписывает исходные config
или service unit. Relative paths разрешаются от WorkingDirectory. Username collision,
unknown credential fields и unknown version fail closed.

Linux agent читает только root-owned allowlisted registry, не принимает arbitrary
path/command, делает managed.describe/snapshot/backup/atomic credentials
replace/restore, D-Bus restart/status, structured health и официальный CLI export.
Linux adoption не переписывает файлы и не перезапускает службу. Повторное confirm
того же preview идемпотентно в живом процессе; после restart при потерянном ответе
нужно сверить список inbounds. Явный re-import синхронизирует credentials и metadata
без потери клиентов только при точном совпадении usernames и без pending/recovery.

Credentials считаются требующими restart. SIGHUP подтверждён только для TLS hosts.
Официальный CLI contract: `binary vpn.toml hosts.toml -c USER -a ADDRESS --format
deeplink|toml`; по сообщению владельца официальный export проверен на staging.
Редактор v1.1.0 меняет только allowlisted `vpn.toml` параметры и упорядоченные
`rules.toml` правила; bind, TLS и протоколы пока read-only. `/clients` читается
только при включённых per-client metrics через буквальный loopback IP; данные не
сохраняются. Включение metrics listener через панель пока не реализовано.

## 3x-ui integration
**Superseded:** прежний план внешней интеграции и read-only adapter отменены новым
TrustTunnel-only scope. Adapter не был подключён к runtime и удалён без миграции БД.
3x-ui используется исключительно как UX-референс.

## Clash/Mihomo integration
**Superseded:** прежний план subscription-интеграции отменён. Код поддержки этих
систем не разрабатывается; TrustTunnel не преобразуется в Clash node.

## Security model
Один admin; bootstrap только локальной CLI. Argon2id, server-side hashed sessions,
HttpOnly/Secure/SameSite=Strict, CSRF + exact Origin, login limiter. Master key вне
SQLite; attachment secrets шифруются Fernet. Секреты не попадают в list API, logs,
AuditEvent или Operation. Sandbox endpoints доступны только при явном включении;
production default fail closed. Полная модель — `docs/SECURITY.md`.

## Configuration apply workflow
`ApplyCoordinator` сериализует операции per inbound внутри процесса, Linux agent
добавляет межпроцессный `flock` и peer PID reservation. Coordinator проверяет drift
по сохранённым hashes, валидирует render до изменения DB/files, создаёт private backup,
пишет same-directory temp с fsync и atomic replace, вызывает provider restart/health,
фиксирует checkpoints в Operation и обновляет applied state/hashes после успеха.

При health/restart failure coordinator сначала сверяет текущие hashes с ожидаемым
или только что записанным состоянием, затем восстанавливает operation-owned backup,
повторяет restart/health и записывает `rolled_back`; при неизвестных внешних правках
или неуспешном восстановлении ставит `needs_recovery`, блокирует новые apply и
предоставляет явный recover action.
Drift блокирует overwrite и допускает re-import только при точном сопоставлении
username и отсутствии pending/recovery либо отмену pending state. Check-drift не
снимает `recovery_required`; apply повторно проверяет hashes после temp-write,
а Linux agent сравнивает ожидаемые hashes всех файлов ещё раз непосредственно перед
replace. При его явном `drift_conflict` backup не восстанавливается поверх внешней
правки. Restart отказывает при drift. Bind address берётся из vpn.toml, public address —
отдельно из root registry; они не подменяют друг друга. Concurrent apply даёт conflict.

Это проверенный sandbox workflow и реализованный Linux adapter. Предыдущий код Linux
adoption/apply по сообщению владельца прошёл ручную Debian staging проверку;
текущие изменения ещё нет. При потерянном ответе после
rename coordinator исходит из возможного replace и делает rollback; при crash после
prepare restore удаляет operation-owned temp. Production требует staging acceptance.

## Текущая инфраструктура
По сообщению владельца, без текущего live detection: Debian 12 / Linux 6.1;
TrustTunnel v1.1.0 production TCP/UDP 443, отдельный staging TCP/UDP 8448,
TunnelUI HTTPS TCP 9443. Web работает как `tunnelui`, агент — root через Unix socket
и D-Bus. В staging `ipv6_available = false`; внешнего IPv6 нет. 3x-ui и WireGuard
существуют на сервере, но вне scope проекта и в этом проходе не затрагиваются.

## Реализовано
- Phase 0 и Phase 1: документы, repository structure, migrations, auth/security,
  app shell, light/dark theme, responsive navigation, clients CRUD и audit.
- Phase 2 sandbox end-to-end: discovery, preview/confirm adoption, inbound/client import,
  encrypted attachments, desired/applied/sync state, создание клиента с attachment,
  credentials render, apply, fake restart/health, durable Operation checkpoints,
  AuditEvent, drift, rollback, `needs_recovery` и recover, last-active-client guard,
  concurrent apply guard, Profiles и inbound-specific fake QR/deep-link/TOML export.
- Attached client меняет username и expiry через отдельный idempotent per-inbound
  apply с проверкой applied metadata и явным partial result. Новая запись с
  заданным сроком помечается managed; мигрированные сроки остаются inert до opt-in.
  Глобальное enabled использует тот же принцип.
  Password rotation выбирает attachment и сохраняет прежний зашифрованный секрет
  до подтверждённого apply; export закрыт при pending/rollback.
- UTC expiry показывает оставшееся время, приближение срока, просроченный, ожидающий
  и подтверждённый отзыв отдельно. `tunnelui expiry-run` — разовый durable проход
  для systemd timer: DB attempt, CAS/lease, deterministic Operation key, cooldown,
  manual retry и проверка expiry/revision под agent lock. Preview и включение в
  Settings явные; миграция и примерный timer не активируют автоматический отзыв.
- Клиента с attachments можно полностью удалить одним подтверждением: сначала
  preflight каждого inbound, затем последовательный apply и проверка фактических
  credentials/hashes, и только после этого удаление DB-записи. Частичный отказ
  сохраняет запись и доступные Operation; повтор с новым key продолжает после
  устранения причины. Отвязка одного inbound остаётся отдельным действием.
- Создание/изменение attachment и global access отклоняют заранее известный drift
  или чужой pending до изменения desired state. При гонке после preflight pending
  виден в UI; preview с fingerprint позволяет явную отмену или apply-pending.
  Для глобального доступа preview показывает все затронутые inbounds; отмена в одном
  может создать pending в другом, которое нужно применить отдельно.
- Sandbox API недоступен без explicit development configuration; это покрыто тестом.
- UI показывает service/config/recovery state, операции, attachments и прямой переход
  к inbound после partial apply; внутренние state labels локализованы.
- Windows Impeccable hook wrapper сохранён и проверялся отдельно; detector текущей
  поверхности возвращает 0 findings.
- Phase 3 Linux boundary code: explicit Linux composition за существующим coordinator,
  root agent с SO_PEERCRED, typed/bounded Unix IPC, root registry, descriptor/no-follow
  files и backup, OS lock, systemd D-Bus, bounded CLI export и structured health.
  Примерные units/tmpfiles/registry и Linux CI job добавлены; фактические staging
  units не следует считать точной копией файлов `packaging/` без сверки.
- Linux adoption: managed.describe, discovery, preview, confirm, encrypted import,
  backup без restart/config write; по сообщению владельца, staging проверил создание
  клиента, apply/rollback, официальный TOML/deeplink/QR и iPhone VPN. Текущие
  regression-тесты проверяют повтор confirm, agent failure, drift, metadata refresh.
- Ветка `codex/trusttunnel-settings-rules` от `phase4/linux-adoption`: чтение и
  ограниченное редактирование подтверждённых параметров TrustTunnel v1.1.0,
  упорядоченные CIDR/client_random_prefix Rules, preview/confirm, применение через
  существующий Operation/backup/restart/health/rollback/recovery. Linux Agent принимает
  только typed `vpn`/`rules` file IDs, без пути из HTTP. Read-only `/clients` доступен
  только на loopback; UI показывает недоступность и область счётчиков.

## Сейчас в работе
PR #3 Credentials/Expiry слит в `phase4/linux-adoption` merge-коммитом `0c21d25`.
PR #4 Settings/Rules/Metrics был слит merge-коммитом `85b67b1` в ветку
`codex/client-credentials-expiry`, поэтому его изменения не попали в целевую ветку.
Корректирующая ветка `codex/complete-settings-rules-integration` создана от
`0c21d25` и обычным merge, без переписывания истории, объединяет состояние
`85b67b1`. До изменения этого документа дерево merge в точности совпало с деревом
проверенного объединённого состояния `a388e4318daddb4e440112368a9821c183fb7511`;
миграция и реализация Credentials/Expiry относительно `phase4/linux-adoption`
не изменились. Сохранены integration fix `35702f2` и документационный коммит
`9934a96`. Локально для итогового состояния: Ruff pass; pytest 155 passed,
13 POSIX-only skipped; TypeScript pass; Vitest 6 passed; Vite production build
pass с известным Ant Design chunk warning; Playwright 7 passed. Linux CI должен
быть повторно запущен в новом PR.
Bind address, TLS keys, listener protocols остаются read-only; для
`credentials.toml`, `vpn.toml` и `rules.toml` используется restart, а SIGHUP
подтверждён только для TLS hosts. Expiry timer остаётся выключенным по умолчанию.

## Следующие задачи
1. Опубликовать корректирующий PR из `codex/complete-settings-rules-integration`
   в `phase4/linux-adoption` и дождаться полного Linux CI.
2. Сливать корректирующий PR только после отдельного разрешения владельца.
3. Отдельно согласовать Debian 12 staging acceptance Credentials/Expiry и
   Settings/Rules/Metrics, включая drift, rollback/recovery и права файлов.
4. Certificate read-only detection, metrics enable с явным loopback guard,
   несколько managed instances в одной панели, backup retention.
5. Installation/upgrade/rollback packaging и отдельный production gate.

## Известные ограничения
- Sandbox host-management остаётся fake; Linux adapter и adoption, по сообщению
  владельца, проверены на отдельном Debian staging до текущих изменений.
- Sandbox locks in-process; Linux agent использует flock per managed ID. Global client
  access по нескольким inbound выполняется
  последовательно, без общей атомарной транзакции. При частичном результате DB-клиент
  сохраняется; UI показывает per-inbound результат, предлагает apply/cancel pending.
  Это операторский recovery workflow, не автоматический cross-inbound rollback.
- Полное удаление также последовательно применяет несколько inbounds: если часть уже
  отозвана, а другая не подтвердилась, клиент не удаляется. Для продолжения нужен
  новый idempotency key после разрешения ошибки; автоматической атомарности нет.
- Expiry scheduler не включается после миграции: оператор предварительно смотрит
  список, включает setting и отдельно устанавливает/включает timer. Debian timer,
  crash replay и реальные время/права ещё не прошли staging acceptance.
- Multi-inbound username/expiry apply последователен. При частичном результате
  attachment имеют разные applied values; UI показывает pending/error и закрывает
  неподтверждённый export. Оператор завершает apply-pending после устранения причины;
  отмена при разных applied identity запрещена. Общего cross-inbound rollback нет.
- Пустой `credentials.toml` не поддерживается TrustTunnel v1.1.0, поэтому последний
  active client нельзя отключить или отвязать без отдельного проверенного решения.
- Fake exporter намеренно не создаёт реальный `tt://`; Linux exporter использует
  официальный CLI, результат подтверждён ручной staging-проверкой владельца.
- Re-import поддерживает только неизменившийся набор username и отказывает при
  pending/recovery; explicit mapping UI нет. Изменения файлов не применяются молча.
- Runtime metrics только read-only и только при уже включённом loopback listener;
  постоянного traffic accounting/quota нет. Certificate management и backup retention
  ещё нет. Редактор не меняет bind, TLS paths, listener protocols и metrics listener.
- Vite сообщает о крупном eager Ant Design chunk (~1.23 MB raw); route splitting отложен.
- Backend TestClient сообщает upstream deprecation warning Starlette/httpx.
- Impeccable skill 4.3.1 имеет доступное обновление 4.5.1; оно не устанавливалось.
- Impeccable 4.3.1 генерирует Windows `commandWindows` для cmd.exe, а Codex 0.159
  выполняет его через активный PowerShell. `.codex/impeccable-hook.cmd` задаёт явную
  cmd.exe-границу, передаёт stdin и сохраняет exit code. После update повторно проверить
  manifest и одобрение через `/hooks`.
- Linux agent умеет managed.describe для adoption, но пока не умеет certificate
  detection, journal API,
  backup retention; QUIC health обозначен `unverified`, а не healthy. IPC request ID
  не дедуплицирует повторное действие; durable idempotency принадлежит coordinator.
- Linux-only tests пропускаются на Windows; GitHub Actions Linux job для PR #4
  прошёл успешно. По сообщению владельца,
  backend на VPS до текущих изменений имел результат 114 passed, 1 warning.
- Проверка hash у агента уменьшает окно гонки с внешним редактором, но не является
  межпроцессной блокировкой для ручного редактора вне TunnelUI: между сравнением
  файлов и atomic rename остаётся короткий TOCTOU интервал. Текущие изменения
  требуют Linux CI и отдельного staging испытания этого сценария.

## Принятые архитектурные решения
- ADR-001 active: host integration через минимальный privileged helper; web без root.
- ADR-002 active: capabilities по точной версии; unknown fail closed.
- ADR-003 active: SQLite v1, UTC timestamps, Alembic migrations.
- ADR-004 active: desired/applied/sync state, потому что DB/files/service не одна транзакция.
- ADR-005 active: Ant Design — component library; Impeccable — design/UX guidance.
- ADR-006 active: credentials требуют restart; TLS reload рассматривается отдельно.
- ADR-007 active: пустой active credential set блокируется для v1.1.0.
- ADR-008 active: неподключённые metrics columns скрыты, а не заполнены фиктивными нулями.
- ADR-009 active: management provider выбирается только explicit composition; production
  default не подменяется FakeSystemProvider.
- ADR-010 active: Operation — durable технический journal, AuditEvent — отдельный
  пользовательский безопасный журнал; ни один не хранит secrets.
- ADR-011 active: Linux agent — только explicit composition за существующим
  coordinator; root-owned registry + UID-checked Unix IPC + per-inbound flock.
- ADR-012 active: health подтверждает service/TCP/TLS, QUIC остаётся `unverified`
  без credential-bearing functional probe; успех health не означает доказанный HTTP/3.
- ADR-013 active (2026-10-08): продукт исключительно для TrustTunnel; прежние планы
  3x-ui/Hysteria2/Mihomo/Clash **superseded**. Ветка `phase4/linux-adoption` сохраняет
  своё Git-имя; прежний «Phase 4 = 3x-ui» отменён, номера завершённых этапов не
  переименованы задним числом.
- ADR-014 active (2026-10-08): external drift не снимает recovery gate; re-import
  явно синхронизирует credentials и metadata из проверенного snapshot только без
  pending changes и при совпадении usernames. Public и bind addresses раздельны.
- ADR-015 active (2026-10-08): `files.commit_credentials` принимает typed ожидаемые
  hashes всех allowlisted файлов; agent повторяет сравнение до rename и возвращает
  `drift_conflict` без записи. Rollback проверяет hashes перед restore; неизвестное
  внешнее состояние требует recovery без перезаписи. После health apply/rollback
  повторно проверяют фактические hashes до объявления успеха. Неизвестный результат ответа
  после rename по-прежнему обрабатывается через существующий rollback/recovery journal.
- ADR-016 active (2026-10-09): client/attachment mutations записывают secret-free
  intent до apply и используют отдельные per-inbound Operation keys. Известный drift
  и чужой pending отклоняются до desired commit; результат `succeeded` требует
  проверенных файлов и health. Полное удаление клиента — последовательный revoke
  с проверкой всех затронутых inbounds до удаления DB-строки, а не CASCADE delete.
  Отмена pending привязана к preview fingerprint и показывает влияние глобального
  enabled на другие inbounds. ADR-004/010 остаются действующими.
- ADR-019 active (2026-10-16; номер устранён при интеграции): редактирование
  TrustTunnel v1.1.0 ограничено
  проверенными параметрами и rules. Linux Agent принимает только typed `vpn`/`rules`,
  атомарно заменяет один файл под allowlisted lock после сравнения hashes всех
  managed файлов; coordinator переиспользует Operation и rollback/recovery.
  Metrics `/clients` остаётся read-only, запрашивается только с loopback literal,
  без сохранения IP/трафика. Это не persistent accounting и не quota.
- ADR-017 active (2026-10-15): срок — policy TunnelUI, а не поле TrustTunnel
  v1.1.0. Миграция 0004 не активирует старые сроки. Разовый CLI + timer работают
  только после preview и явного включения; каждая попытка имеет durable lease,
  cooldown и Operation key. Последний credential блокируется. Прежнее ожидание,
  что один только renderer означает автоматический отзыв, **superseded**.
- ADR-018 active (2026-10-15): username/expiry являются desired значениями Client,
  а фактические username/expiry/password фиксируются per Attachment после health
  и hash verification. Несколько inbound применяются по очереди; partial не
  скрывается. Старый encrypted password удерживается до успеха rotation. Профиль
  нельзя экспортировать, пока желаемое и применённое расходятся.
Изменённые решения помечаются superseded и дополняются заменой, история не стирается.

## Deployment notes
В текущем проходе deployment не выполнялся. Dev работает на loopback и explicit
sandbox configuration. По сообщению владельца, предыдущая версия уже установлена
на отдельном Debian staging и доступна по HTTPS TCP 9443.
Примерный web unit — `User=tunnelui`; socket root:tunnelui 0660, agent root с
ограниченным registry/paths и typed operation IDs. Панель не занимает 443.
DB backup и master-key backup хранятся раздельно. `docs/DEPLOYMENT.md` содержит
примерные units, процедуру подготовки immutable release и повторный staging gate
для текущего diff, а не разрешение на production install.

## Changelog
- 2026-10-27: выявлено, что PR #4 был слит в ветку PR #3, а не в
  `phase4/linux-adoption`. Подготовлена корректирующая ветка от merge-коммита PR #3
  `0c21d25`: она объединяет проверенное состояние `85b67b1`, сохраняет исправления
  `35702f2` и документацию `9934a96`, не дублируя Credentials/Expiry. VPS,
  deployment и expiry timer не затрагивались.
- 2026-10-16: PR #3 и PR #4 объединены в интеграционной ветке обычным merge;
  сохранены credential/expiry и settings/rules/metrics paths, единый coordinator,
  блокировки, journal и recovery. Runtime SHA `35702f2` прошёл GitHub Actions
  `Checks` run #25: backend, Linux Agent и frontend/Playwright.
- 2026-10-15: отдельная ветка редактора v1.1.0 настроек/Rules и read-only
  `/clients`; новые typed agent file operations, sandbox tests и UI. Linux CI
  прошёл; staging для этого изменения ещё требуется. Локальный совместный прогон с PR #3
  выявил позиционный конфликт `coordinator.apply`; исправлен именованным вызовом.
- 2026-10-15: локально добавлены миграция 0004, safe username/expiry apply,
  per-attachment password rotation, opt-in expiry-run/timer example, статусы,
  Settings и профили, regression/E2E. Linux Agent/VPS не менялись.
- 2026-10-09: client lifecycle: safe multi-inbound revoke/delete, attachment limits,
  global access intents, preflight drift/pending, compare-and-cancel preview и
  UI-пути восстановления. Миграция 0003; Linux/VPS не затронуты.
- 2026-10-01: Phase 0, version-pinned research, архитектура, документы и project-local Impeccable.
- 2026-10-01: Phase 1 и offline core Phase 2; базовые frontend/backend tests.
- 2026-10-02: UX hardening, полный baseline прогон и документация.
- 2026-10-02: Windows hook исправлен через project-local cmd wrapper с stdin/exit-code passthrough.
- 2026-10-05: Phase 2 sandbox соединён end-to-end: adoption, attachments, coordinator,
  durable operations/recovery, drift/rollback, profiles/export и Playwright coverage.
- 2026-10-05: Impeccable critique/audit/harden/polish: локализованы состояния,
  устранена неоднозначность multi-inbound export и добавлен прямой recovery path.
- 2026-10-07: Phase 3 Linux boundary реализован за существующими interfaces:
  root-owned agent/registry/socket, safe file operations, D-Bus, locks, CLI exporter,
  health, Linux CI configuration и unit examples. Production/VPS не затронуты.
- 2026-10-08: по сообщению владельца, предыдущий Linux adoption прошёл ручную Debian
  staging проверку; текущий локальный проход усилил drift/re-import/metadata и
  поправил Linux UI. Продуктовый scope сужен до TrustTunnel, старые интеграционные
  планы помечены superseded; production/VPS этим проходом не затронуты.
- 2026-10-09: устранено Linux-only mobile overflow в деталях inbound: responsive
  ширина Drawer больше не анимируется через `transition: all`, вложенные таблицы
  прокручиваются внутри себя; Playwright проверяет 320/375/390/768 px и сохраняет
  геометрию нарушителей в тексте assertion при регрессии.
- 2026-10-09: устранена гонка повторного открытия формы клиента после duplicate:
  Drawer получает новую React session, E2E ждёт закрытия и ограничивает локаторы
  активным dialog. Linux CI при E2E failure сохраняет trace, screenshot,
  error-context и безопасную геометрию Drawer на 7 дней.
