# TunnelUI

Главный технический документ. Состояние на 2026-10-07. Product context — `PRODUCT.md`,
визуальная система — `DESIGN.md`. Production, VPS и работающие сервисы в этом проходе
не подключались и не изменялись.

## Цель проекта
Поддерживаемая русскоязычная web-панель управления VPN-доступом: TrustTunnel под
непосредственным управлением TunnelUI, Hysteria2 и другие поддерживаемые протоколы
через 3x-ui, Mihomo через subscription 3x-ui. Первая страница — «Входящие»;
dashboard и traffic quota не входят в текущий scope.

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
fail closed с 503; скрытого fake fallback нет. Linux code пока не проходил Debian
staging. Подробности — `docs/ARCHITECTURE.md`.

## Компоненты
- `backend/src/tunnelui`: API, domain, services, repositories, integrations, system adapters.
- `backend/migrations`: Alembic 0001 и 0002; схема не создаётся через `create_all`.
- `backend/tests`: temp directories, fakes и mocks; реальный systemd не вызывается.
- `frontend/src`: app shell, shared API/types и features auth/inbounds/clients/profiles/audit/settings.
- `frontend/e2e`: Chromium-пути Phase 1/2, recovery, mobile и accessibility smoke checks.
- `.agents/skills/impeccable`: project-local design/UX skill; Ant Design остаётся библиотекой компонентов.
- `backend/src/tunnelui/agent`: versioned IPC, registry, OS lock, безопасные файлы,
  systemd D-Bus, health, официальный CLI exporter и socket server.
- `packaging`: примерные systemd/socket units, tmpfiles, registry и env; не установлены.

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
Sandbox workflow: Detect → Parse → Preview → Confirm adoption → backup → сохранить
metadata/hashes и зашифрованные attachments. Adoption не переписывает исходные config
или service unit. Relative paths разрешаются от WorkingDirectory. Username collision,
unknown credential fields и unknown version fail closed.

Linux agent читает только root-owned allowlisted registry, не принимает arbitrary
path/command, делает snapshots/backup/atomic credentials replace/restore, D-Bus
restart/status, structured health и официальный CLI export. Linux discovery/adoption
ещё отсутствует, поэтому существующий service на VPS не импортировался.

Credentials считаются требующими restart. SIGHUP подтверждён только для TLS hosts.
Официальный CLI contract: `binary vpn.toml hosts.toml -c USER -a ADDRESS --format
deeplink|toml`; `--help` установленного на сервере binary ещё не проверялся. Metrics
и `/clients` относятся к Phase 3; listener по умолчанию только loopback.

## 3x-ui integration
Проверен tag v3.8.5, commit `7ef22f94c950ff09f0870e2295fa65ad5968742c`.
Реализован изолированный read-only adapter list/get с TLS verification, timeout,
response limit, запретом redirects и MockTransport tests. В runtime/web он не подключён.
Никакого доступа к `x-ui.db`. Write sync и reconciliation — Phase 4.

## Clash/Mihomo integration
Будет использовать subscription-механизм 3x-ui только для принадлежащих ему
протоколов. TrustTunnel не преобразуется в Clash node. Порты и пути не hardcode.
Subscription storage, выдача URL и UI ещё не реализованы.

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

При health/restart failure coordinator восстанавливает operation-owned backup,
повторяет restart/health и записывает `rolled_back`; при неуспешном восстановлении
ставит `needs_recovery`, блокирует новые apply и предоставляет явный recover action.
Drift блокирует overwrite и допускает re-import только при точном сопоставлении
username либо отмену pending state. Concurrent apply возвращает видимую ошибку.

Это проверенный sandbox workflow и реализованный Linux adapter, но Linux side effects
пока проверены только кодом/тестами, не на Debian staging. При потерянном ответе после
rename coordinator исходит из возможного replace и делает rollback; при crash после
prepare restore удаляет operation-owned temp. Production требует staging acceptance.

## Текущая инфраструктура
Только данные пользователя, без live detection: Debian 12, `fi-zanevka-pnv`,
`150.241.228.208`, `zanevka-pnv.duckdns.org`; TrustTunnel v1.1.0 в
`/opt/trusttunnel`, `trusttunnel.service`, TCP/UDP 443; WireGuard UDP 51820,
SSH TCP 22, Certbot TCP 80; 3x-ui v3.8.5. Эти сервисы не трогались.

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
  Примерные units/tmpfiles/registry и Linux CI job добавлены, но не развернуты.

## Сейчас в работе
Текущий проход — Phase 3 Linux boundary hardening без VPS/production. Phase 2 sandbox
не переписан. Windows local tests и Linux CI configuration проверяются; фактический
Linux CI run и Debian staging acceptance ещё не выполнялись. Локальный прогон
2026-10-07: Ruff pass; pytest **99 passed, 10 Linux-only skipped**; TypeScript pass;
Vitest **3 passed**; Vite build pass; Playwright Chromium **6 passed**;
Impeccable detector Profiles **0 findings**. Windows sandbox `esbuild spawn EPERM`
потребовал повторить Vitest/Vite/Playwright в разрешённом project-local контексте.

## Следующие задачи
1. Завершить оставшийся Phase 2 product scope: durable expiry scheduler/jobs и
   безопасное применение username/expiry для уже привязанных клиентов.
2. Запустить Linux CI и Debian 12 staging acceptance на отдельном test endpoint;
   проверить socket/permissions, D-Bus, CLI, health, rollback/recovery и unit sandboxing.
3. Завершить Linux discovery/adoption и production bootstrap; затем Phase 3 metrics
   `/clients`, Rules и Profiles runtime. CLI exporter написан, но не проверен на VPS.
4. Phase 4: 3x-ui auth/API, external inbound sync и Mihomo subscription.
5. Phase 5: installation/upgrade/rollback packaging, staging acceptance и deployment.

## Известные ограничения
- Host-management в подключённом sandbox остаётся fake. Linux adapter существует,
  но не исполнялся на Debian и не подключался к VPS; 3x-ui остаётся read-only adapter.
- Sandbox locks in-process; Linux agent использует flock per managed ID. Global client
  access по нескольким inbound выполняется
  последовательно, без общей атомарной транзакции и cross-inbound recovery.
- Expiry учитывается renderer, но автоматического durable scheduler/revoke пока нет.
- Username/expiry attached client нельзя менять через текущий UI; metadata можно.
- Пустой `credentials.toml` не поддерживается TrustTunnel v1.1.0, поэтому последний
  active client нельзя отключить или отвязать без отдельного проверенного решения.
- Fake exporter намеренно не создаёт реальный `tt://`; Linux exporter использует
  официальный CLI, но runtime binary на VPS ещё не проверен.
- Re-import поддерживает только неизменившийся набор username; explicit mapping UI нет.
- Нет metrics/runtime traffic, rules editor, certificate management, backup retention,
  3x-ui writes и Mihomo subscriptions.
- Vite сообщает о крупном eager Ant Design chunk (~1.23 MB raw); route splitting отложен.
- Backend TestClient сообщает upstream deprecation warning Starlette/httpx.
- Impeccable skill 4.3.1 имеет доступное обновление 4.5.0; оно не устанавливалось.
- Impeccable 4.3.1 генерирует Windows `commandWindows` для cmd.exe, а Codex 0.159
  выполняет его через активный PowerShell. `.codex/impeccable-hook.cmd` задаёт явную
  cmd.exe-границу, передаёт stdin и сохраняет exit code. После update повторно проверить
  manifest и одобрение через `/hooks`.
- Linux agent пока не умеет discovery/adoption, certificate detection, journal API,
  backup retention; QUIC health обозначен `unverified`, а не healthy. IPC request ID
  не дедуплицирует повторное действие; durable idempotency принадлежит coordinator.
- Linux-only tests пропускаются на Windows; GitHub Actions job настроен, но фактический
  Linux run ещё не подтверждён. Docker Linux engine и WSL здесь недоступны.

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
Изменённые решения помечаются superseded и дополняются заменой, история не стирается.

## Deployment notes
Deployment не выполнялся. Dev работает на loopback и explicit sandbox configuration.
Примерный web unit — `User=tunnelui`; socket root:tunnelui 0660, agent root с
ограниченным registry/paths и typed operation IDs. Панель не занимает 443.
DB backup и master-key backup хранятся раздельно. `docs/DEPLOYMENT.md` содержит
примерные units и staging gate, а не инструкцию немедленного production install.

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
