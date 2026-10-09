# TunnelUI

Главный технический документ. Состояние на 2026-10-09. Product context — `PRODUCT.md`,
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
- `backend/migrations`: Alembic 0001 и 0002; схема не создаётся через `create_all`.
- `backend/tests`: temp directories, fakes и mocks; реальный systemd не вызывается.
- `frontend/src`: app shell, shared API/types и features auth/inbounds/clients/profiles/audit/settings.
- `frontend/e2e`: Chromium-пути Phase 1/2, recovery, mobile и accessibility smoke checks.
- `.agents/skills/impeccable`: project-local design/UX skill; Ant Design остаётся библиотекой компонентов.
- `backend/src/tunnelui/agent`: versioned IPC, registry, OS lock, безопасные файлы,
  systemd D-Bus, health, официальный CLI exporter и socket server.
- `packaging`: примерные systemd/socket units, tmpfiles, registry и env; фактическую
  конфигурацию staging нужно сверять отдельно, примеры не считать её копией.

## Модель данных
`Admin`, `AdminSession`, `Client`, `Inbound`, `Attachment`, `Operation`, `AuditEvent`.
Client глобален; credential и connection limits принадлежат attachment. Attachment
хранит encrypted password, desired/applied/sync state и revision. Operation хранит
kind, state, idempotency key, request fingerprint, ожидаемые/результирующие hashes,
safe error code и timestamps. В Operation нет password, config plaintext, tt://,
API token или private key. AuditEvent — отдельная сущность с allowlisted summary.

Миграция 0002 добавляет operation journal и attachment state. Operation states:
`pending → preparing → backed_up → writing → applying → checking → succeeded`;
ошибка после записи проходит `rolling_back → rolled_back`, а неподтверждённый rollback
заканчивается `needs_recovery`. До записи возможен `failed`.

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
deeplink|toml`; по сообщению владельца официальный export проверен на staging. Metrics
и `/clients` относятся к Phase 3; listener по умолчанию только loopback.

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
- Attached client разрешает безопасное изменение display name/comment; username,
  expiry и global enabled требуют отдельного apply workflow и пока заблокированы.
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

## Сейчас в работе
Текущий проход завершает Linux adoption, drift/metadata safety и TrustTunnel-only
scope локально, без VPS/SSH/deployment. Phase 2 coordinator и Operation journal
сохранены. GitHub Actions Linux выявил transient horizontal overflow после смены
desktop viewport на mobile при открытом Ant Design Drawer. Исправление ограничивает
анимацию Drawer transform/shadow и даёт таблицам деталей собственный horizontal scroll.
Текущий локальный Windows прогон frontend: TypeScript pass, Vitest 3 passed,
Vite build pass, Playwright 6 passed, включая `needs_recovery` Drawer на
320/375/390/768 px. Повторный Linux CI и staging acceptance ещё не выполнялись.

## Следующие задачи
1. Завершить оставшийся Phase 2 product scope: durable expiry scheduler/jobs и
   безопасное применение username/expiry для уже привязанных клиентов.
2. Запустить Linux CI и повторить Debian 12 staging acceptance для текущих изменений:
   drift, metadata re-import, agent failure, rollback/recovery, unit compatibility.
3. Завершить оставшийся TrustTunnel scope: metrics `/clients`, Rules, certificate
   read-only detection и поддержка нескольких managed instances в одной панели.
4. Installation/upgrade/rollback packaging и отдельный production gate.

## Известные ограничения
- Sandbox host-management остаётся fake; Linux adapter и adoption, по сообщению
  владельца, проверены на отдельном Debian staging до текущих изменений.
- Sandbox locks in-process; Linux agent использует flock per managed ID. Global client
  access по нескольким inbound выполняется
  последовательно, без общей атомарной транзакции и cross-inbound recovery.
- Expiry учитывается renderer, но автоматического durable scheduler/revoke пока нет.
- Username/expiry attached client нельзя менять через текущий UI; metadata можно.
- Пустой `credentials.toml` не поддерживается TrustTunnel v1.1.0, поэтому последний
  active client нельзя отключить или отвязать без отдельного проверенного решения.
- Fake exporter намеренно не создаёт реальный `tt://`; Linux exporter использует
  официальный CLI, результат подтверждён ручной staging-проверкой владельца.
- Re-import поддерживает только неизменившийся набор username и отказывает при
  pending/recovery; explicit mapping UI нет. Изменения файлов не применяются молча.
- Нет metrics/runtime traffic, rules editor, certificate management и backup retention.
- Vite сообщает о крупном eager Ant Design chunk (~1.23 MB raw); route splitting отложен.
- Backend TestClient сообщает upstream deprecation warning Starlette/httpx.
- Impeccable skill 4.3.1 имеет доступное обновление 4.5.0; оно не устанавливалось.
- Impeccable 4.3.1 генерирует Windows `commandWindows` для cmd.exe, а Codex 0.159
  выполняет его через активный PowerShell. `.codex/impeccable-hook.cmd` задаёт явную
  cmd.exe-границу, передаёт stdin и сохраняет exit code. После update повторно проверить
  manifest и одобрение через `/hooks`.
- Linux agent умеет managed.describe для adoption, но пока не умеет certificate
  detection, journal API,
  backup retention; QUIC health обозначен `unverified`, а не healthy. IPC request ID
  не дедуплицирует повторное действие; durable idempotency принадлежит coordinator.
- Linux-only tests пропускаются на Windows; GitHub Actions Linux job настроен, но
  этот проход не запускал удалённый CI. По сообщению владельца, backend на VPS до
  текущих изменений имел результат 114 passed, 1 warning.
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
Изменённые решения помечаются superseded и дополняются заменой, история не стирается.

## Deployment notes
В текущем проходе deployment не выполнялся. Dev работает на loopback и explicit
sandbox configuration. По сообщению владельца, предыдущая версия уже установлена
на отдельном Debian staging и доступна по HTTPS TCP 9443.
Примерный web unit — `User=tunnelui`; socket root:tunnelui 0660, agent root с
ограниченным registry/paths и typed operation IDs. Панель не занимает 443.
DB backup и master-key backup хранятся раздельно. `docs/DEPLOYMENT.md` содержит
примерные units и повторный staging gate для текущего diff, а не разрешение на
production install.

## Changelog
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
