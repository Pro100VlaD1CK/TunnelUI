# TunnelUI

Главный технический документ. Состояние на 2026-10-02; product context — PRODUCT.md,
визуальная система — DESIGN.md. Никаких подключений или изменений production в этом проходе.

## Цель проекта
Поддерживаемая русскоязычная панель управления VPN: непосредственно TrustTunnel,
внешний 3x-ui для Hysteria2, его subscription для Mihomo. Первая страница — Входящие;
dashboard и traffic quota не входят в scope.

## Текущая архитектура
Модульный монолит FastAPI / Pydantic 2 / SQLAlchemy 2 / Alembic / SQLite;
React / TypeScript / Vite / Ant Design / TanStack Query. Backend без root.
API → services → repositories/domain → providers. Долгие privileged операции
будут выполняться агентом через локальный Unix socket с root-owned allowlist.
Разделение desired/applied состояния — принятое направление, полная orchestration ещё
не реализована. Нет общей транзакции DB/files/systemd. Рабочий web сейчас управляет
глобальными записями клиентов без привязок; host control недоступен.
Подробности: docs/ARCHITECTURE.md, проверенные источники: docs/RESEARCH.md.

## Компоненты
- backend/src/tunnelui: API, services, repositories, domain, integrations, system.
- backend/migrations: воспроизводимая схема, не create_all при запуске приложения.
- backend/tests: temp directories, mocks, FakeSystemProvider; без systemd.
- frontend/src: app, shared, features/auth, clients, inbounds, audit, settings.
- docs: архитектура, безопасность, запуск и UX-проверки.
- .agents/skills/impeccable: project-local design/UX skill; не component library.

## Модель данных
Admin (Argon2id), AdminSession (hash случайного token, CSRF, TTL), Client
(unique username, display_name, enabled, comment, UTC expiry/timestamps), Inbound
(kind, managed registry ID, metadata, hashes), Attachment (unique client/inbound,
encrypted credential, limits, sync state), AuditEvent (allowlisted summary).
Apply operation journal и external integration config — следующие миграции.
Client глобален, credentials принадлежат attachment. SubID также является секретом.

## TrustTunnel integration
Проверен tag v1.1.0, commit fab5b8353a19332f935fa30869307d37d4a898d1.
В production разрешается только после Detect → Parse → Preview → backup → Confirm
adoption → metadata/import encrypted credentials; initial files не переписываются.
Будущий helper должен получать WorkingDirectory и структурированный ExecStart из systemd D-Bus;
shell unit text нельзя исполнять или разбирать как произвольную команду.
Offline parser разрешает относительные paths от WorkingDirectory. Native discovery,
root-owned allowlist и Linux path security ещё не реализованы.
Credentials требуют restart. SIGHUP подтверждён только для TLS hosts.
CLI export: binary vpn.toml hosts.toml -c USER -a ADDRESS --format deeplink|toml.
--help установленного на сервере binary ещё НЕ проверен: доступа к серверу не было.
Неизвестные версии fail closed до проверки capabilities и --help.
Metrics /clients opt-in, только loopback; traffic за время процесса, IP наблюдаемый,
не гарантированно последний. Runtime-сбор metrics относится к Phase 3.

## 3x-ui integration
Проверен tag v3.8.5, commit 7ef22f94c950ff09f0870e2295fa65ad5968742c.
Bearer API tokens поддерживаются; routes /panel/api/inbounds/list,
/panel/api/clients/get/:email, /clients/add, /clients/:email/attach,
/clients/update/:email относительно настраиваемого web base path.
Cookie login требует CSRF; предпочитаем Bearer. Никакого доступа к x-ui.db.
Реализован изолированный XUIReadOnlyAdapter (list/get) с httpx MockTransport tests,
проверкой TLS, timeout, лимитом response и запретом redirects. В web он не подключён.
sync_decision — только классификация состояния, не engine синхронизации.
Полная integration и sync в Phase 4. Проверка существования и конфликтов до создания;
network timeout на write не означает, что создание не произошло: re-read before retry.
Внешний transport/security остаётся read-only, badge External · 3x-ui.

## Clash/Mihomo integration
Только subscription 3x-ui, только принадлежащие ему протоколы. TrustTunnel не
превращается в Clash node. Получать links/settings через API или явно настроенный
public base; subClashPath/subClashURI настраиваемые, порт и путь не hardcode.
План: URL/subId шифровать и выдавать только явным действием, no-store.
Subscription storage, получение URL и UI действий ещё не реализованы.

## Security model
Один admin, bootstrap через локальную CLI, без публичного setup endpoint.
Argon2id, server-side sessions, HttpOnly/Secure/SameSite=Strict, CSRF и Origin
на mutations, login rate limit. Auth secrets никогда не в localStorage.
Master key отдельным файлом вне SQLite. Fernet authenticated encryption;
ошибка ключа — отказ, не создание нового ключа поверх существующей DB.
Никаких request-body logs, raw upstream errors, secrets в audit/validation errors.
Система прав, threat model и ограничения: docs/SECURITY.md.

## Configuration apply workflow
Целевой production workflow: per-inbound lock → повторный hash всех source files → schema validation → private
backup → same-directory temp → flush/fsync → replace → directory fsync (POSIX)
→ restart → bounded health → сохранить applied hashes. При ошибке восстановить
backup, restart, health; отдельно distinguish rolled_back / rollback_failed.
Сейчас SandboxApplyService меняет только credentials: сравнивает hashes всех файлов,
сохраняет backup, делает fsync/atomic replace, вызывает fake restart/health и rollback.
Lock in-process на экземпляр сервиса; events в памяти; updated hashes возвращаются
вызывающему коду, не persist в DB. Повторные health probes с timeout ещё отсутствуют.
SandboxAdoptionService сохраняет metadata, hashes и encrypted attachments в DB после
preview/confirm/backup; файлы и service не меняет. Оба use case не доступны по HTTP.
Многофайловая atomic transaction невозможна; durable journal/recovery и Linux
lock/ownership/TOCTOU hardening — обязательные ворота до production helper.
Конфликт drift блокирует overwrite и требует re-import. Restore только confirm.

## Текущая инфраструктура
Данные предоставлены пользователем, live detection не выполнялся.
Debian 12; fi-zanevka-pnv; 150.241.228.208; zanevka-pnv.duckdns.org.
TrustTunnel v1.1.0 /opt/trusttunnel/trusttunnel_endpoint; trusttunnel.service,
WorkingDirectory=/opt/trusttunnel, ExecStart binary vpn.toml hosts.toml.
vpn.toml: 0.0.0.0:443, credentials.toml, rules.toml, direct forwarding;
HTTP1/2/QUIC; metrics закомментированы. TLS Let's Encrypt.
Не трогать TCP/UDP 443, WireGuard UDP 51820, SSH TCP 22, Certbot TCP 80.
3x-ui v3.8.5 уже обслуживает Hysteria2 и subscriptions.

## Реализовано
- Исследованы официальные version-pinned исходники, архитектура зафиксирована до кода.
- Impeccable project-local: npm package 4.1.0, installed skill 4.3.1, engine 0.1.5.
  Установка через локальный pnpm runner (npx отсутствовал); системные версии не менялись.
  Создан .codex/hooks.json. Пользователь должен одобрить hook через /hooks;
  доверие не выдано автоматически. Skill доступен в project-local каталоге.
- Phase 0: PROJECT/AGENTS/PRODUCT/DESIGN, architecture/security/deployment/research docs,
  repository structure и выбранное пользователем code-first направление.
- Phase 1: FastAPI + React/Vite/TypeScript/Ant Design/TanStack Query, Alembic 0001,
  SQLite, CLI migrate/create-key/create-admin. DB создаётся миграцией, не startup create_all.
- Argon2id, DB sessions с hashed tokens/TTL/rotation/logout, HttpOnly/Secure/SameSite,
  pre-login CSRF + Origin, bounded single-process login limiter, безопасные validation errors.
- Реальный API и UI глобальных клиентов: create/edit/enable/disable/delete, uniqueness,
  revision CAS, поиск/filter/pagination, аудит успешных операций. Для attached client
  edit/delete fail closed: orchestration доступа не подключена.
- Меню с первой страницей Входящие, light/dark, mobile drawer; реальный список Audit.
  Inbounds — read-only список metadata из БД, без live health. Profiles/Rules и большая
  часть Settings честно обозначены как недоступные; host status не симулируется.
- Phase 2 offline core: TrustTunnel parse/relative paths/capabilities, secret-free preview,
  confirmed adoption с backup/encrypted import, credential render с enabled/expiry/limits,
  duplicate/unknown-field rejection, drift detection, atomic replace и fake rollback.
- Дополнительные pure/adapter основы Phase 3/4: Rule validation, /clients payload parser,
  loopback metrics validation, официальный export argv, XUI read-only adapter/sync classifier.
  Это не готовые runtime integrations или UI-разделы.
- UX hardening: ошибка внутри confirmation, конфликт revision с явным закрытием без
  сохранения и refresh, уведомление session expiry, доступный loading status, reduced
  motion, коррекция pagination после удаления, без пустых колонок будущих metrics.
- Воспроизводимые requirements.lock/pnpm-lock.yaml и локальные тесты; CI workflow
  добавлен, но его remote execution в этом проходе не проверялся.

## Сейчас в работе
Текущий проход завершён: контрольный прогон и Impeccable audit/polish текущей admin
surface выполнены. Phase 1 функциональна в локальном scope; Phase 2 частична,
host управление закрыто. Следующая работа — journal/recovery и координация Phase 2.
Проверки 2026-10-02: Ruff pass, pytest **62 passed**, TypeScript/Vite build pass,
Vitest **2 passed**, Playwright Chrome **5 passed**. Detector: 0 findings;
ручной audit/polish и границы проверки — docs/UI-REVIEW.md.

## Следующие задачи
1. Следующий этап Phase 2: durable operation journal и единая per-inbound координация
   adoption/apply; recovery после прерывания, reconciliation desired/applied.
2. API/UI preview-confirm adoption и attachments на явно выбранном sandbox provider,
   честный pending/applied/error, полноценный audit операций и ошибок.
3. Тесты совместной работы DB/files при сбоях, repeated confirm/apply и конкурентных
   операциях. Реальный Linux helper только после этих gates и отдельного host scope.
4. Expiry scheduler с durable jobs, retries, per-inbound serialization.
5. Phase 3 metrics/profiles/rules, Phase 4 3x-ui/sync/Mihomo.
6. Phase 5 Linux helper/systemd packaging и staging acceptance; отдельный запрос deployment.

## Известные ограничения
- До Phase 5 production управление заблокировано; sandbox tests не являются доказательством
  безопасности root helper или доступности listener на реальном Debian.
- v1.1.0 читает credentials через TOML array-of-tables; ноль активных клиентов нельзя
  представить пустым credentials-файлом. Отказываем до отдельного проверенного решения.
- Parser upstream удаляет кавычки и trim при чтении credentials; для новых credentials
  запрещаем кавычки/управляющие символы/краевые пробелы вместо обещания round-trip.
- Unknown credential fields не отбрасывать молча: import/render должен остановиться.
- TLS expiry detection, systemd discovery и --help живого binary пока не проверены.
- Rate limit в одном процессе; multi-worker deployment требует общего limiter.
- Expiry сейчас фильтруется renderer и отображается UI; scheduler и автоматического
  отзыва VPN-доступа нет. Enabled глобальной записи не равно effective VPN state.
- Audit DB сейчас фиксирует успешные auth/client/adoption операции; failed operations,
  backup/restart/rollback требуют durable audit. Sandbox apply events только в памяти.
- Locks не межпроцессные, adoption и apply не имеют общей registry/lock manager;
  нет crash recovery и защищённого обхода filesystem descriptors. Production запрещён.
- List UI пока не показывает реальные attachments, sessions/traffic/IP; метрики не собираются.
- Runtime export CLI/QR/TOML, rules editing, certificates, restore/retention, 3x-ui writes
  и Mihomo subscriptions не подключены. --help установленного binary не проверен.
- Vite предупреждает о большом Ant Design chunk; backend TestClient выдаёт upstream
  deprecation warning о httpx. Это известные предупреждения, не падения проверок.

## Принятые архитектурные решения
- ADR-001 active: native host integration через отдельный минимальный helper, web без root.
- ADR-002 active: known capabilities по точной проверенной версии, unknown fail closed.
- ADR-003 active: SQLite для первой версии, UTC timestamps, миграции Alembic.
- ADR-004 active: desired/applied/sync state; DB и service не единая транзакция.
- ADR-005 active: Ant Design component library, Impeccable только design/UX guidance.
- ADR-006 active: credentials restart; tls reload отдельно. Не добавляем ExecReload в unit.
- ADR-007 active: пустой набор active credentials блокируется на v1.1.0 до решения.
- ADR-008 active: Phase 1 скрывает колонки ещё не подключённых metrics вместо постоянных
  пустых значений; при подключении добавить их с явной семантикой counters и observed IP.
Изменения ADR добавляются с superseded и ссылкой на замену, не стираются.

## Deployment notes
Ничего не деплоить в этом проходе. Dev — loopback, fake/offline providers.
Будущий web unit User=tunnelui; root-owned agent socket разрешён только этому UID.
Отдельный HTTPS endpoint панели не занимает 443 TrustTunnel. TLS termination/порт
нужно согласовать отдельно; HTTP dev только на localhost с explicit insecure cookie.
DB backup и master key backup раздельно; потеря key делает secrets невосстановимыми.
Инструкции разработки: docs/DEPLOYMENT.md.

## Changelog
- 2026-10-01: Phase 0: исходники v1.1.0/v3.8.5 исследованы, архитектура и ограничения
  зафиксированы, Impeccable установлен локально. Production untouched.
- 2026-10-01: реализованы Phase 1 и offline core Phase 2, 62 backend tests,
  базовые frontend unit/e2e; независимые Impeccable review A/B, UX fixes.
- 2026-10-02: продолжение без повторной реализации; полный контрольный прогон,
  regression e2e для UX fixes, исправление измеренного контраста primary action
  и selected navigation в dark theme; обновлена документация по фактическому коду.
