# Локальная разработка и будущий deployment

Этот проход ничего не деплоит. Нужны Python 3.11+ и Node 20.19+/22.12+,
pnpm 11.19.0. В текущем окружении использованы Python 3.11.9, Node 24.19.0.
Команды ниже выполняются из корня TunnelUI. Нельзя подставлять production DB/files.

## Backend (PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
.venv\Scripts\python.exe -m pip install --no-deps -e ./backend
$env:TUNNELUI_DEVELOPMENT = 'true'
$env:TUNNELUI_SECURE_COOKIE = 'false'
$env:TUNNELUI_ORIGIN = 'http://127.0.0.1:5173'
.venv\Scripts\tunnelui.exe migrate
.venv\Scripts\tunnelui.exe create-key
.venv\Scripts\tunnelui.exe create-admin
.venv\Scripts\python.exe -m uvicorn tunnelui.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```
create-admin интерактивно запрашивает пароль, не передавайте его в argv/history.
create-key нужен для secret import; обычный Phase 1 client CRUD секретов не создаёт.
Linux/macOS: `.venv/bin/python`, `.venv/bin/tunnelui`; env через `export NAME=value`.
Не используйте несколько backend workers: текущий login limiter in-process.

## Frontend
В отдельном терминале:
```powershell
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend dev
```
Открыть http://127.0.0.1:5173. Vite proxy /api → 127.0.0.1:8000 сохраняет same-origin
в браузере. Origin должен совпадать с браузером; не смешивать localhost и 127.0.0.1.
Frontend секретов не содержит; login cookie хранит браузер, CSRF только в памяти.

Для проверки production build локально:
```powershell
pnpm --dir frontend build
$env:TUNNELUI_ORIGIN = 'http://127.0.0.1:8000'
.venv\Scripts\python.exe -m uvicorn tunnelui.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```
`frontend/dist` отдаёт backend, маршруты UI hash-based. HTTP допустим только в
explicit development. Все реальные production настройки должны вернуть Secure и HTTPS.

## Тесты
```powershell
.venv\Scripts\python.exe -m pytest backend/tests -q
.venv\Scripts\python.exe -m ruff check backend scripts
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend test:e2e
```
E2E использует установленный Chrome (Playwright channel chrome) и два изолированных
loopback sandbox на ports 8765/8766 (второй — credential/expiry scenario),
создаёт отдельные временные SQLite в .runtime и fixture admin test-admin с
заведомо тестовым паролем из scripts/e2e_server.py. Production/admin DB не используется.
Сервер теста только 127.0.0.1, access log отключён; Playwright останавливает его
после тестов. При аварийном завершении Windows disposable temp directory может
остаться: .runtime исключён из Git. Ни systemd, ни network integrations не нужны.
Screenshots: frontend/test-results (gitignored). Тестовые данные синтетические.
При падении E2E Linux CI загружает только этот каталог с trace, screenshot,
error-context и безопасной геометрией Drawer; retention — 7 дней.
Linux CI устанавливает Chrome через Playwright; это не часть production packaging.

## Impeccable
Official install: `npx impeccable install --providers=codex --scope=project`.
В этом Windows окружении npm/npx отсутствовали, поэтому использован локальный runner:
`pnpm --dir .tools --config.node-linker=hoisted add impeccable`, затем
`.tools/node_modules/.bin/impeccable.cmd install --providers=codex --scope=project`.
Global package/environment не менялся; package managers использовали обычный download cache.
Installed skill .agents/skills/impeccable, hook .codex/hooks.json.
**Откройте /hooks и одобрите project hook самостоятельно.** Его trust не выдаётся
автоматически. Skill доступен после reload/следующего хода Codex.
`/impeccable init` и другие slash commands — инструкции агента, не shell commands.
Native engine binary gitignored; launcher при отсутствии может скачать platform engine.

## Linux boundary package (не инструкция для production install)
`packaging/systemd/` содержит примерные web, agent и socket units;
`tunnelui-expiry.service` и `.timer` — отдельный oneshot worker с пятиминутным
интервалом, **не включённый автоматически**;
`packaging/tmpfiles.d/tunnelui.conf` — runtime/private каталоги;
`packaging/agent.toml.example` — root-owned managed registry;
`packaging/tunnelui.env.example` — web environment. Это примеры в репозитории,
а не подтверждённая копия текущих VPS units. Web unit работает как `tunnelui`, слушает только loopback
8080; agent socket принадлежит root:tunnelui (0660), agent — отдельный root
process. Публичный HTTPS ingress не входит в этот набор. TCP/UDP 443, TCP 80/22 и
UDP 51820 не занимаются панелью.

Примеры намеренно требуют ручной подстановки путей, пользователя, hostname и
managed ID после проверки фактического Debian staging. `ReadWritePaths` agent unit
сейчас показывает только один `/opt/trusttunnel`; для нескольких managed services
каждый путь и private backup root добавляются осознанно. Registry и его parent
должны быть root-owned и не group/world-writable, конфиги — regular files без
symlink/hardlink и с допустимыми owner/mode. Не менять права Let's Encrypt private
key ради агента: certificate detection пока только read-only.

Включение Linux composition требует одновременно `TUNNELUI_AGENT_SOCKET`,
`TUNNELUI_AGENT_MANAGED_ID`, `TUNNELUI_AGENT_EXPECTED_VERSION` и
`TUNNELUI_AGENT_PUBLIC_ADDRESS`; без них production-default management fail closed.
Sandbox включается отдельно только development configuration. Прямой запуск
`tunnelui-agent` на Windows отвергается. Не использовать несколько web workers:
login limiter остаётся process-local.

По сообщению владельца проекта, Debian 12 staging TrustTunnel v1.1.0 на TCP/UDP
8448 и TunnelUI HTTPS на TCP 9443 уже вручную подтвердили discovery, adoption,
apply, D-Bus restart/health, rollback, официальный TOML/deeplink, QR и подключение
iPhone. Production TrustTunnel остаётся на TCP/UDP 443. Эти данные не получены
текущим запуском Codex и не являются разрешением на deployment.

После текущих изменений нужен повторный staging acceptance на том же отдельном
экземпляре: Linux CI, registry/unit compatibility, внешний drift, metadata re-import,
отказ агента, rollback и `needs_recovery`. Не выполнять проверки на production 443.
Особенно сверить фактические unit ExecStart/WorkingDirectory, owner/mode, socket
SO_PEERCRED, официальный CLI и health. Sample units не делают ветку production-ready.

## Миграция 0004 и включение expiry (только будущая процедура)
До обновления сделать согласованные backup SQLite и master key, проверить restore
на копии, затем выполнить 0004 на копии БД и убедиться, что старые `expires_at`
имеют `expiry_managed=false`, а `expiry_settings.enabled=false`. Миграция не
перезаписывает credentials и не запускает TrustTunnel. Старый release после 0004
должен использовать сохранённый pre-upgrade DB snapshot; понижение Alembic на
работающей БД не является runbook rollback, так как оно удаляет историю новых
applied/expiry state. Не смешивать backend и agent разных release.

После отдельной staging проверки можно установить timer unit из того же release.
Сначала в Settings → «Сроки доступа» просмотреть всех затрагиваемых клиентов,
включая старые просроченные записи и последний активный credential. Включение
настройки само не запускает worker. Только затем оператор отдельно включает
`tunnelui-expiry.timer`; `Persistent=true` делает один catch-up после простоя.
`tunnelui expiry-run` выполняется от `tunnelui`, через текущий root-owned agent,
без root для web/worker. Если настройка выключена, CLI не вызывает host actions.
Ошибки и cooldown доступны в Settings; «Повторить» планирует следующую попытку,
не делает немедленный restart. Для остановки отключить timer и настройку;
уже подтверждённо отозванный доступ не восстанавливается без явного продления.
До staging acceptance не устанавливать и не включать timer на текущем VPS.

## Воспроизводимое обновление отдельного staging release (проект процедуры)

Эта процедура описывает будущий операторский запуск, а не выполнялась Codex. До
применения сверить реальные unit/override с примерными `packaging/` и проверить
Linux CI. Не переключать рабочий Git checkout in-place: подготовить новый каталог
release из конкретного commit SHA рядом с работающим, собрать frontend и установить
backend/agent из одного SHA. Сохранить manifest SHA, checksums пакета и миграции.

Проверочный порядок для объединённой версии:

1. Зафиксировать SHA и health текущего TunnelUI `53da595`, HTTPS 9443 и staging
   TrustTunnel 8448; production 443 оставить вне теста.
2. Создать согласованные защищённые копии SQLite 0003 и master key, не работая с
   оригинальной DB в проверках.
3. На копии выполнить 0003→0004, повторный upgrade, `PRAGMA integrity_check`,
   сверить `alembic_version`, клиентов, attachments и выключенный expiry setting.
4. Подготовить новый immutable worktree/release из точного прошедшего CI SHA.
5. Установить locked Python dependencies и собрать frontend из того же SHA.
6. Выполнить import/read-only smoke от UID `tunnelui`, включая static assets и БД.
7. Подготовить отдельный набор version-pinned systemd overrides для web и agent;
   реальные unit/registry/path/UID сверить до переключения.
8. Контролируемо переключить только TunnelUI web/agent и сохранить обратимый путь
   к прежнему release; TrustTunnel на 443/8448 этим шагом не перезапускать.
9. Проверить HTTPS 9443, login, agent socket, SO_PEERCRED, версии backend/agent и
   совпадение static asset SHA.
10. На staging 8448 проверить username/expiry/password rotation, Profiles и
    ручной expiry preview; timer оставить выключенным.
11. На staging 8448 проверить Settings/Rules preview/apply, read-only metrics и
    сохранение неизвестных TOML полей, owner/group/mode.
12. Отдельно воспроизвести drift между preview/apply, restart/health failure,
    rollback, потерянный IPC response и `needs_recovery`.
13. Подтвердить, что production 443 не менялся и не перезапускался, а его hashes,
    service state и клиентский доступ соответствуют исходному состоянию.
14. Проверить, что `expiry_settings.enabled=false`, timer не enabled/active; включение
    допускается только отдельным решением после просмотра затрагиваемых клиентов.

Подготовить файлы с читаемыми для service UID режимами, а не наследовать `umask 077`
у root Git checkout: parent directories должны иметь traversal для `tunnelui`, код
быть доступен на чтение, исполняемые файлы — на исполнение. Проверить это именно
запуском import/read-only smoke от `tunnelui`, включая `PYTHONPATH`, static dir и
`.venv`; не менять режимы master key, DB или private TLS key по общему правилу для
кода. Root registry и agent executable оставить root-owned; socket/backup права
проверить по `docs/SECURITY.md`.

До переключения снять согласованную копию SQLite и отдельную защищённую копию
master key; проверить миграцию 0004 на копии DB. Откат к старой версии после миграции
должен использовать совместимую pre-upgrade DB snapshot, если старый код не понимает
новую схему. Подготовить version-pinned настройки `PYTHONPATH` одновременно для web
и agent, плюс `TUNNELUI_STATIC_DIR` от того же release. После локальной проверки
транзакционно переключить набор unit overrides по заранее утверждённому runbook и
перезапустить только TunnelUI web/agent; TrustTunnel production/staging не входят в
шаг обновления приложения. Задание запускать через systemd/локальный операторский
скрипт с журналом и проверяемым exit status, чтобы потеря SSH-сессии не оставила
неопределённый набор половинчатых ручных команд.

После переключения проверить health API и HTTPS 9443, login, чтение inbounds,
совпадение backend/agent version и static asset SHA. Затем на отдельном staging
TrustTunnel 8448 проверить read-only drift/preview, тестового клиента, apply,
rollback/recovery и отсутствие секретов в журналах. При отказе вернуть оба unit
overrides и static dir на прежний release, восстановить DB snapshot при несовместимой
миграции, проверить HTTPS и agent socket. Старый release не удалять до завершения
acceptance. Production 443 не рестартовать автоматически и не использовать как тест.
