# Admin surface: Impeccable critique / audit / harden / polish

Дата: 2026-10-05. Scope: auth, shell, TrustTunnel adoption/inbounds, clients,
attachments/apply/recovery, Profiles, Audit и deferred Settings/Rules. Ant Design и
плотная административная компоновка сохранены.

## Результат audit

Детерминированный detector запускался один раз:
`.agents/skills/impeccable/scripts/impeccable.cmd detect --json frontend/src`.
Exit 0, JSON `[]`, 0 findings и 0 suppressions. Две независимые оценки использовали
исходники, шесть актуальных Playwright screenshots и passed `.last-run.json`.

| Измерение | Балл / 4 | Основание и граница |
|---|---:|---|
| Accessibility | 3 | Labels, skip-link, focus, text+color states, reduced motion; полного AT/WCAG прогона нет |
| Performance | 2 | Нет лишней animation/media, но eager Ant chunk около 1.23 MB raw |
| Responsive | 3 | 390px shell/drawers/controls и отсутствие document overflow проверены; wide data tables используют внутренний scroll |
| Theming | 4 | Обе темы, custom tokens и primary contrast проверены Playwright |
| Implementation integrity | 3 | Реальные sandbox operations и честные deferred states; production adapters отсутствуют явно |
| **Итого** | **15/20 — Good** | Оценка текущего локального scope, не accessibility или production certification |

## Critique

Nielsen: **28/40 — Good**. Сильная сторона поверхности — объяснение backup, atomic
replace, restart, health, rollback и last-client protection до опасного действия.
Основная нагрузка была связана с raw state enums, partial apply без прямого recovery
пути и неоднозначным profile export при нескольких inbound.

## Закрытые находки

- Profile export теперь требует конкретный `inbound_id`; строка каждого attachment
  создаёт QR/deep-link/TOML именно для показанного источника.
- Partial create/apply показывает, что уже сохранено, и ведёт прямо к inbound operation journal.
- Desired/applied/sync, operation kind/result и management state получили русские labels.
- Adoption показывает два честных этапа вместо недостижимого третьего шага.
- «Проверить drift» больше не обещает мгновенную проверку из menu; действие открывает
  экран проверки. Неиспользуемый inbound query-filter не имитируется.
- Clipboard denial не создаёт unhandled rejection: профиль остаётся открыт для ручного действия.
- Drawer section headings приведены к уровню из DESIGN.md.
- Metadata привязанного клиента можно редактировать; credential-affecting поля явно
  заблокированы до отдельного apply-aware workflow.

Ранее закрытые находки сохранены: confirmation errors внутри modal, revision conflict
recovery, session-expiry feedback, pagination после удаления, accessible loading,
reduced-motion tokens и primary label contrast в обеих темах.

## Оставшиеся findings и границы

- **P2 Performance:** route features загружаются eager, итоговый Ant chunk около
  1.23 MB raw / 396 kB gzip. Нужен измеренный optimize pass с route-level lazy loading;
  warning не скрывать механическим chunk split.
- **P2 Responsive evidence:** wide tables доступны через внутренний horizontal scroll,
  но отдельная screen-reader/zoom/browser matrix не выполнена.
- **P3 Polish:** быстрые последовательные success messages могут перекрывать header
  на mobile. Это transient nonblocking состояние; dedupe/top offset отложены.
- Actions menu растёт с количеством attachments. До подтверждённого масштаба сохраняется
  текущая плотная модель; группировку и bulk actions проверять по реальным workflows.

## Harden evidence

Playwright покрывает adoption, создание и apply, last-client guard, concurrent action,
drift/re-import, rollback, `needs_recovery`, expired session, network failure, revision
conflict, pagination, keyboard skip, contrast и 390px overflow. Backend tests покрывают
все Operation checkpoints, failed rollback/recovery, no-sandbox fail-closed, secret
redaction, profile source selection и metadata-only edit attached client.

Live Impeccable overlay не использовался: готовой browser surface не было. Проверка
основана на воспроизводимых Playwright artifacts и source review. VPS, real TrustTunnel,
3x-ui и production не подключались.

## Финальный контрольный прогон

| Проверка | Результат |
|---|---|
| Backend Ruff | Pass |
| Backend pytest | **79 passed**, одно upstream TestClient/httpx warning |
| TypeScript | Pass |
| Vite production build | Pass, известный large-chunk warning |
| Frontend Vitest | **3 passed** |
| Playwright Chromium | **6 passed** |

Impeccable installed skill: 4.3.1; доступна 4.5.0, обновление не выполнялось.
