---
name: TunnelUI
description: Плотная административная панель VPN
colors:
  primary: "#176b87"
  dark-primary-seed: "#54b6cc"
  dark-primary-label: "#101820"
  light-bg: "#f3f5f7"
  light-surface: "#ffffff"
  light-text: "#172b3a"
  dark-bg: "#101820"
  dark-surface: "#17232d"
  dark-text: "#e6edf3"
typography:
  body:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "14px"
    lineHeight: 1.5
  heading:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "24px"
    fontWeight: 600
rounded:
  control: "6px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
---

## Overview
Ant Design, Operate mode. Визуальное направление задано пользователем: современная
плотная панель с привычной навигацией, без декоративной альтернативной концепции.
Impeccable init/shape/craft применяются как workflow skill, не runtime dependency.

## Colors
Сдержанный сине-зелёный accent для выбранного раздела и primary action. Семантические
success/warning/error Ant Design с текстом. Themes через ConfigProvider algorithm;
не менять только фон, оставляя controls от другой темы. Dark primary seed #54b6cc
проходит darkAlgorithm; rendered accent может отличаться от seed. Button.primaryColor
и Menu.darkItemSelectedColor = #101820: белый текст на светлом accent не достигает
4.5:1. Danger button сохраняет отдельный стандартный dangerColor.

## Typography
Одна локальная системная sans с кириллицей. Body 14px, heading 24px, labels 13–14px.
Tabular numerals для времени/счётчиков; monospace только hostname/id/code.
На mobile input text 16px, чтобы избежать auto-zoom. Не загружать внешний шрифт
ради административной панели.

## Layout
Sidebar 216px, header 56px, content padding 24px/16px mobile. Toolbar → table,
не card-inside-card. До 768px sidebar заменяется drawer; таблица скроллится внутри
своего контейнера, не вся страница. Controls toolbar wrap, drawer width min(480px,100vw).
Строки compact, pagination 25 с возвратом на последнюю непустую страницу после
удаления. Client table min-width 820px, scroll внутри таблицы, actions справа.
Mobile кнопки минимум 44px по высоте, icon buttons 44px шириной.
Загрузка Skeleton с доступным role=status; ошибки с кнопкой retry.

## Elevation & Depth
Borders разделяют данные; тени только у overlay стандартной библиотеки.

## Shapes
6px controls; никаких больших capsule containers. Стандартные affordances Ant.

## Components
Tables: search, enabled filter, refresh, actions dropdown. В текущей версии сортировка
username задаётся сервером, интерактивные sort controls отсутствуют. Ещё не подключённые
колонки metrics скрыты, под таблицей объяснение. После подключения для временно
недоступных значений показывать «—», а не нули.
Forms: видимые labels, inline validation, pending submit disabled, form reset on close.
Drawers для create/edit. Modals для delete/disable/rotate/restart/restore с ясными
последствиями. Status badges не полагаются только на цвет.
Недоступные функции disabled + текст причины, не кнопки-заглушки с fake success.
Подтверждение save не означает применённый VPN config: показывать attachment/apply state.
Confirmation errors показывать внутри активного modal. Revision conflict в editor
предлагает «Закрыть без сохранения и обновить список», не отбрасывает edits молча.
Session expiry объясняется на экране входа. Save показывает feedback, но не обещает apply.
Adoption использует два честных этапа: обнаружение и проверка/импорт. После partial
apply интерфейс даёт прямой переход к inbound и Operation journal. В журнале технические
enum переводятся в операторские русские labels; error code показывается как безопасный
результат, без secret details. Desired и applied всегда показаны раздельно.
Profiles связывает каждую строку и export action с конкретным inbound. QR/deep-link/TOML
создаются только явным действием; clipboard failure оставляет профиль открытым для
ручного копирования. Fake exporter всегда явно помечен как тестовый.
При Linux CLI export UI показывает обычный профиль; текст не выдаёт sandbox QR за
реальную ссылку. Наличие официального exporter не означает готовность production.
Drawer section headings используют единый 17px уровень; bare oversized h2 внутри
операционных drawers не применять. На mobile wide tables остаются в собственном scroll.
UI states: loading, empty, failed/retry, ready, submitting, validation error, disabled.
Motion минимальная: prefers-reduced-motion отключает Ant motion token и декоративный
Skeleton shimmer, сохраняя статическое сообщение загрузки; глобального .01ms reset нет.

## Do's and Don'ts
Показывать данные и действия, а не декоративные KPI. Не скрывать ошибки. Не отображать
secrets по умолчанию. Не растягивать заголовки и пустые hero areas. Не копировать 3x-ui.
