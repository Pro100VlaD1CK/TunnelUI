# Assessment B: detector и технический audit

Исторический отчёт. Итоговый прогон и закрытие перечисленных находок:
[UI-REVIEW.md](UI-REVIEW.md), 2026-10-02. Ниже сохранены исходные evidence и ограничения.

Дата: 2026-10-01. Target: `frontend/src`. Независимая Assessment B; выводы
Assessment A не читались. Режим Operate: плотная административная панель,
Ant Design и локальная системная sans закреплены brief пользователя.

## Детерминированный scan

Ровно один запуск:

```text
.agents/skills/impeccable/scripts/impeccable.cmd detect --json frontend/src
```

- Exit code: **0**.
- Raw JSON: `.impeccable/detector.json`, содержимое `[]`.
- Findings: **0**; уникальных rule names: **0**; locations: **0**.
- Severity counts детектора: P0=0, P1=0, P2=0, P3=0.
- False positives детектора: нет, поскольку findings отсутствуют.
- Это результат статического детектора, не подтверждение WCAG или отсутствия
  функциональных дефектов. Повторный scan в родительском контексте не требуется.
- `.impeccable/critique/ignore.md` не найден при чтении; ignore findings не применялись.

## Browser evidence и ограничения

Live browser/overlay **не выполнен**: при передаче задачи локальный сервер не
запущен; задача ограничена source audit и имеющимися screenshot fixtures.
Новый browser tab, mutable injection preflight, inject `detect.js`, browser console
scan и показ пользовательского overlay не выполнялись. Live overlay unavailable;
это fallback по сохранённым изображениям, а не live-browser assessment.
Сервер не запускался, cleanup сервера не требуется. Raw JSON сохранён намеренно;
других временных файлов Assessment B не создавала. Hook trust не менялся.

Просмотрены `frontend/test-results/clients-desktop-dark.png`,
`clients-mobile-dark.png`, `login-mobile-light.png`; timestamps около
15:00–15:01 2026-10-01. Mobile screenshot показывает внутренний горизонтальный
scroll таблицы и сохраняет видимую кнопку действий; login помещается по ширине.

**Freshness caveat:** рабочие исходники менялись во время assessment. В частности,
текущий `Shell.tsx` уже содержит theme для Menu, условный mobile Drawer с
`destroyOnHidden` и обработчик skip link с `preventDefault()`/`focus()`.
Старый desktop screenshot показывает слишком тёмные подписи sidebar, но это
не доказательство дефекта текущего Menu. Старый error-context содержит timeout
при возврате из mobile в desktop; текущий test уже использует scoped locator
`getByRole('complementary')`, а Drawer условный. Не считать старую ошибку
подтверждённой текущей регрессией. Нужен финальный прогон родителя.

## Технический audit исходников

Implementation integrity: **pass с оговорками**. UI сохраняет единый Ant Design
язык, реальные операции отделены от deferred integration, отсутствуют fake KPI,
VPN-секреты и декоративные marketing элементы. Системная sans, обычные таблицы
и компактные controls намеренны; не предлагать декоративную замену библиотеки,
типографики или расширение desktop controls только ради универсальных эвристик.

Оценки ориентировочные по source + fixtures, не live certification:

| Измерение | Балл / 4 | Основание |
|---|---:|---|
| Accessibility | 2 | Labels, именованные icon buttons, focus и skip link есть; loading не объявляется |
| Performance | 3 | Server pagination 25, нет тяжёлых media/effects; bundle/runtime profiling не выполнялся |
| Responsive | 3 | Drawer, wrapping toolbar, внутренний scroll; полная keyboard/touch/zoom проверка не выполнена |
| Theming | 3 | ConfigProvider algorithms и согласованные tokens; новый Menu theme требует свежей визуальной проверки |
| Implementation integrity | 3 | Последовательный product UI, остаются отдельные edge cases |
| **Итого** | **14/20** | **Good, предварительно** |

Ручные findings: **3 P2**, P0=0/P1=0/P3=0. Они не являются выводами детектора.

1. **[P2] Loading не имеет доступного текстового статуса.**
   `frontend/src/shared/QueryState.tsx:5` возвращает только `Skeleton active`.
   Проверен установленный официальный исходник Ant Design **6.6.5**
   (`frontend/node_modules/antd/es/skeleton/Skeleton.js`): он рендерит
   оформленные div без status/busy текста. Пользователь screen reader при
   начальной загрузке не получает ясного объяснения ожидания. Добавить внешний
   `role="status"` с доступным «Загрузка…», скрыть декоративный skeleton от AT;
   по необходимости пометить область `aria-busy`. Связано с WCAG 4.1.3;
   severity требует подтверждения assistive-tech прогоном. Команда: harden.

2. **[P2] Глобальное сокращение всех анимаций до .01ms не задаёт reduced-motion альтернативу.**
   `frontend/src/app/styles.css:43–44`. При этом `QueryState.tsx:5` включает
   активный Skeleton. Глобальное правило меняет timing всех компонентов,
   включая бесконечный shimmer, и затрудняет оценку поведения overlay/feedback.
   Не утверждается, что flashing реально измерен. Отключить декоративный shimmer
   адресно и использовать поддерживаемый Ant motion token для reduced-motion;
   оставить статические loading/status cues. Команда: harden/animate.

3. **[P2] После удаления последнего клиента на последней странице page не корректируется.**
   `frontend/src/features/clients/Clients.tsx:23,37`: успешное удаление только
   инвалидирует query, controlled `current: page` остаётся прежним. Например,
   26 записей → page 2 → удалить единственную строку: offset=25 при total=25,
   и таблица может показывать empty-state, хотя на первой странице данные есть.
   При получении нового total ограничить page допустимым диапазоном или
   перейти назад после удаления последней строки. Проверить boundary 26→25
   на fake API; live воспроизведение в этом assessment не выполнялось.
   Команда: harden.

## Отклонённые подозрения и положительные свойства

- `QueryState` error Alert не нуждается в дополнительном role для самого
  объявления ошибки: `antd/es/alert/Alert.js` уже устанавливает `role="alert"`.
- Hex literals в корневых CSS variables / ConfigProvider — определения tokens,
  а не автоматически theme drift. Системный Segoe UI закреплён DESIGN.md.
- Размеры controls около 32px на mobile — кандидат на удобство касания, но не
  автоматическое нарушение WCAG AA: критерий 24px и spacing требует измерения.
  Декоративного увеличения плотного desktop UI не рекомендуется.
- Статусы имеют текст; недоступные метрики показывают «—», а не вымышленные нули.
- Есть явные labels, inline validation, pending submit, retry, empty guidance,
  CSRF-aware API слой и предупреждение об отсутствии VPN-привязок.
- Первичная область списка остаётся table; card-inside-card и лишних KPI нет.

Следующий ограниченный проход: harden перечисленных edge cases, свежая проверка
desktop/mobile обеих тем и keyboard-flow, затем polish. Без production действий.
