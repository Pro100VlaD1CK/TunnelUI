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
session agent не принимает paths или commands. Компрометация web UID ограничивается
root-owned registry capabilities и socket peer UID. Это ограничение ещё не проверено
на Debian staging и не равно полной защите при компрометации root.
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

Linux adapter/agent, D-Bus provider, OS locks, socket-activated units и sample
registry реализованы, но на Windows не исполнялись с реальным host boundary.
Production startup/import не подключался; fake exporter продолжает обслуживать
sandbox, а Linux exporter запрашивает официальный CLI только после явной Linux
configuration. Runtime `--help` и SHA бинарника на VPS не получены.

Agent принимает versioned typed JSON через Unix socket, проверяет SO_PEERCRED UID,
размеры сообщений и request ID. Root registry, managed files, binary и backup
проходят descriptor-relative nofollow и owner/mode/link checks. Запросы не содержат
arbitrary paths/commands. stdout CLI может содержать пароль/tt:// и возвращается
только на явный запрос; stderr не логируется. Operation/AuditEvent остаются
без secrets. QUIC health имеет статус `unverified`: TCP/TLS probe не доказывает
работу UDP/HTTP3. Agent не раскрывает journal через IPC. Сбой release lock и
неопределённый D-Bus outcome требуют operator review по Operation state.

Примерные systemd units используют `NoNewPrivileges`, `PrivateTmp`,
`ProtectHome`, `ProtectSystem=strict`, защиту kernel/control groups,
`RestrictNamespaces`, ограничение адресных семейств и пустые capabilities.
Web unit получает запись только в `/var/lib/tunnelui`; agent — только в
allowlisted managed workdir, `/var/lib/tunnelui-agent` и `/run/tunnelui`.
Agent оставлен root для записи root-owned файлов и доступа к system bus; web
остаётся отдельным UID. `MemoryDenyWriteExecute` и остальные опции ещё нужно
проверить на Debian с фактическими Python/dbus/TrustTunnel binary, прежде чем
считать units пригодными к установке. Socket unit создаёт 0660 root:tunnelui;
tmpfiles заранее создаёт root-owned runtime/backup каталоги без world write.

## Обязательные проверки до production
- Запустить Linux-only agent tests и полный CI на Linux, затем Debian 12 staging с
  отдельным тестовым TrustTunnel instance: socket permissions/SO_PEERCRED, root registry,
  systemd D-Bus, unit sandboxing, file owners/modes, rollback/recovery и CLI `--help`.
- Проверить endpoint-specific health и QUIC с тестовым credential, если требуется
  подтверждение UDP/HTTP3; текущий probe подтверждает service/TCP/TLS, QUIC — unknown.
- Реализовать безопасный Linux discovery/adoption и unit compatibility review;
  sample registry не является автоматическим импортом работающего endpoint.
- Проверить backup retention, restart/recovery после power loss, audit tamper
  resistance, master key recovery и поведение при пропавшем ответе IPC.
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
