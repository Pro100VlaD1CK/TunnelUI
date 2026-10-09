# Проверенные источники — 2026-10-01

Исходники скачаны только для исследования в gitignored .research. Сервер не опрашивался.

## TrustTunnel v1.1.0
Commit `fab5b8353a19332f935fa30869307d37d4a898d1`.
- [CLI source](https://github.com/TrustTunnel/TrustTunnel/blob/v1.1.0/endpoint/src/main.rs):
  positional vpn/hosts, -c, -a, --format toml|deeplink, --version; default deeplink.
  Запрещено использовать generation prefix option при read-only export: оно меняет rules.
- [Configuration](https://github.com/TrustTunnel/TrustTunnel/blob/v1.1.0/CONFIGURATION.md):
  listen_protocols.http1/http2/quic, main_hosts, client array-of-tables, rule array-of-tables.
- [Settings parser](https://github.com/TrustTunnel/TrustTunnel/blob/v1.1.0/lib/src/settings.rs):
  deserialize_clients требует array-of-tables; demangle_toml_string удаляет quotes/trim.
  Поэтому нулевой набор и непрозрачные credential transformations — отдельные риски.
- [Metrics](https://github.com/TrustTunnel/TrustTunnel/blob/v1.1.0/METRICS.md):
  /clients array username/ip/sessions/inbound/outbound; 404 при per_client_metrics=false.
  ip произвольный наблюдаемый при нескольких connections. Lifetime в docs не доказательство
  persisted accounting: UI говорит «с запуска endpoint», после restart возможен reset.
- [Rules source](https://github.com/TrustTunnel/TrustTunnel/blob/v1.1.0/lib/src/rules.rs):
  ordered first-match; default allow; special deny при отсутствующем client_random и
  наличии prefix rules. Mask matching upstream использует minimum length; UI вводит
  более строгую одинаковую длину prefix/mask, чтобы избежать неоднозначности.
- [Endpoint README](https://github.com/TrustTunnel/TrustTunnel/blob/v1.1.0/endpoint/README.md):
  SIGHUP reload TLS hosts only. Credentials restart, не hot reload.
Runtime --help установленного binary ещё требуется перед production adoption.

## 3x-ui v3.8.5 — историческое исследование, scope superseded
Этот раздел сохранён как история раннего решения. С 2026-10-08 TunnelUI управляет
только TrustTunnel; перечисленные API не используются и интеграция не планируется.
Commit `7ef22f94c950ff09f0870e2295fa65ad5968742c`.
- [API auth/routes](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/internal/web/controller/api.go):
  Bearer token, scopes, /panel/api prefix относительно web base, inbounds/clients/setting.
- [Clients](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/internal/web/controller/client.go):
  GET list/get/:email/subLinks/:subId/links/:email; POST add/update/:email/:email/attach.
  Не использовать obsolete addClient endpoints из старых гайдов.
- [Inbounds](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/internal/web/controller/inbound.go):
  list, get/:id; transport/security read-only в первой интеграции TunnelUI.
- [Login](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/internal/web/controller/index.go):
  csrf-token и POST login с middleware. Предпочтён API Bearer, не scraping login form.
- [Settings](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/internal/web/service/setting.go):
  subClashEnable/subClashPath/subClashURI; paths могут генерироваться при setup.
- [Subscription implementation](https://github.com/MHSanaei/3x-ui/tree/v3.8.5/internal/sub):
  Clash/Mihomo реализуется upstream; строить/получать URLs по actual settings,
  не копировать YAML generator. Hysteria2 поддерживается upstream.

## Стек и UX
- [FastAPI security](https://fastapi.tiangolo.com/tutorial/security/)
- [SQLAlchemy 2 ORM](https://docs.sqlalchemy.org/en/20/orm/quickstart.html)
- [Ant Design tokens](https://ant.design/docs/react/customize-theme/)
- [TanStack Query](https://tanstack.com/query/latest/docs/framework/react/overview)
- [Impeccable install](https://github.com/pbakaus/impeccable#installation):
  project scope, Codex hooks trust через /hooks; команды — инструкции skill в чате,
  `/impeccable init` не shell command. Current craft deprecated alias for new-work.
