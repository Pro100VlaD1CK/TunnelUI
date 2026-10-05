# Security model и ворота production

## Что работает сейчас
Один admin создаётся локальной CLI, пароль минимум 12 символов, Argon2id.
Opaque session token хранится в cookie HttpOnly/Secure/SameSite=Strict; в SQLite
только SHA-256 token. Login меняет session ID, logout удаляет серверную сессию,
TTL 8 часов. CSRF связан с session (включая анонимную pre-login); все mutations
проверяют exact Origin и X-CSRF-Token. Authenticated APIs fail closed.
Только explicit loopback development разрешает отключить Secure cookie.
Login: 5 попыток за 5 минут по peer IP; X-Forwarded-For не доверяется. Это limiter
одного процесса, не multi-worker/shared limiter. Bootstrap закрыт при существующем admin.

SecretBox использует authenticated Fernet encryption, ключ вне БД, POSIX 0600;
create-key O_EXCL и не перезаписывает существующий ключ. На Windows требуется
обычный private user ACL; проверка chmod здесь не эквивалентна Windows ACL audit.
Attachment password импортируется зашифрованно, его repr/preview/list скрыт.
Validation response не содержит input. Audit summaries из allowlist, без request
details. API ответы no-store; CSP запрещает remote scripts, embeds и framing.
style-src unsafe-inline необходим Ant Design CSS-in-JS; script unsafe-inline нет.
Browser localStorage содержит только тему. Секретов там нет.

## Threat model
Злоумышленник из браузера не получает filesystem/systemd интерфейс. Даже с admin
session будущий helper не принимает paths или commands. Компрометация web UID
ограничивается root registry capabilities; ограничить socket peer UID и payload.
SQL injection закрывается SQLAlchemy bind parameters; race edits — revision CAS;
duplicate usernames — DB uniqueness. Для client с attachments metadata edit разрешён,
а username/expiry/global enabled идут только через apply-aware paths; delete fail closed.
Секретный master key и DB вместе позволяют decrypt; защищать отдельно и backup
раздельно. Privileged endpoint binary экспортирует секретный stdout — не логировать.

## Граница текущего результата
Sandbox adoption/apply подключены к HTTP только при explicit development flag и
fixture registry. Без него management endpoints отвечают `sandbox_unavailable`; fake
provider не выбирается автоматически. Operation journal durable в SQLite, а AuditEvent
остаётся отдельной сущностью; оба содержат только allowlisted metadata и safe codes.
Backup files содержат credentials и создаются только в private sandbox directory;
retention и production restore policy ещё не реализованы.

Нет native systemd provider, agent executable, sudoers, service units или production
startup. Locks in-process, не межпроцессные. Runtime `--help` и SHA установленного
binary не получены. Fake exporter намеренно выдаёт не настоящий `tt://`.

## Обязательные проверки до Phase 5
- Linux descriptor-based no-follow path traversal, ownership, permissions, hardlinks,
  parent directory checks, per-inbound OS locks и privilege separation tests.
- Helper-owned journal, OS locks и startup reconciliation после process/power loss;
  SQLite Operation checkpoints уже проверяют sandbox crash boundaries.
- Health должен включать service state и проверку требуемых listeners/TLS, bounded retry;
  active systemd state сам по себе не подтверждает готовность TCP/UDP endpoint.
- Подтверждённый способ отключить последнего клиента v1.1.0, либо безопасно
  изолировать inbound с отдельным явным подтверждением. Не вставлять dummy credentials.
- Ограничение размера request до парсинга на reverse proxy, shared rate limit при
  масштабировании, безопасная доверенная proxy policy, HTTPS и firewall.
- Протокол key rotation/recovery и backup retention; tamper resistance audit.
- Expiry durable scheduler и visible failed jobs; attachment apply UI уже есть,
  credential-affecting edit существующего attachment пока ограничен.
- 3x-ui URL allowlist/SSRF policy для реального подключения; adapter пока не HTTP endpoint.
  Не следовать redirect с Bearer token, TLS verification включена, bounded response.
- Dependency vulnerability audit и staging acceptance на Debian 12.

Нельзя считать это production hardening certificate. Рабочие сервисы не проверялись
и не изменялись, доступ к серверу не выполнялся.
