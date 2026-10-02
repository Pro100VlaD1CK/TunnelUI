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
E2E использует установленный Chrome (Playwright channel chrome) и port 8765,
создаёт отдельную временную SQLite в .runtime и fixture admin test-admin с
заведомо тестовым паролем из scripts/e2e_server.py. Production/admin DB не используется.
Сервер теста только 127.0.0.1, access log отключён; Playwright останавливает его
после тестов. При аварийном завершении Windows disposable temp directory может
остаться: .runtime исключён из Git. Ни systemd, ни network integrations не нужны.
Screenshots: frontend/test-results (gitignored). Тестовые данные синтетические.
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

## Production (пока только проект)
Native systemd web User=tunnelui и отдельный root-owned agent; никаких root web
workers и произвольного sudo. До внедрения helper недопустимо подключать panel к
/opt/trusttunnel. Не занимать TCP/UDP 443, 80, 22 или UDP 51820.
Для панели отдельный HTTPS endpoint/порт, согласованный вне этого прохода.
Реальные units, install/upgrade/rollback packaging и helper socket — Phase 5.
