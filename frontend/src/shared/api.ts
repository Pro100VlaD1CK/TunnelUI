let csrf = '';
export class ApiError extends Error {
  constructor(public code: string, public status: number) { super(code); }
}

export async function refreshCsrf() {
  const response = await fetch('/api/auth/csrf', { credentials: 'same-origin' });
  if (!response.ok) throw new ApiError('connection_failed', response.status);
  csrf = (await response.json()).csrf_token;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const mutation = options.method && options.method !== 'GET';
  if (mutation && !csrf) await refreshCsrf();
  const response = await fetch(`/api${path}`, {
    ...options, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(mutation ? { 'X-CSRF-Token': csrf } : {}), ...options.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ code: 'connection_failed' }));
    if (response.status === 401 && path !== '/auth/login' && path !== '/auth/me') {
      window.dispatchEvent(new Event('session-expired'));
    }
    if (data.code === 'csrf_failed') csrf = '';
    throw new ApiError(data.code ?? 'request_failed', response.status);
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json();
  if (data.csrf_token) csrf = data.csrf_token;
  return data as T;
}

const messages: Record<string, string> = {
  invalid_credentials: 'Неверное имя пользователя или пароль.',
  login_rate_limited: 'Слишком много попыток входа. Повторите через 5 минут.',
  duplicate_username: 'Такой username уже существует. Укажите другой.',
  revision_conflict: 'Запись уже изменена. Обновите список и откройте её заново.',
  attached_client_requires_apply: 'У клиента есть привязки. Удаление и изменения username, срока или глобального состояния требуют безопасного применения конфигурации.',
  csrf_failed: 'Защитный токен устарел. Повторите действие.',
  session_expired: 'Сессия истекла. Войдите снова.',
  authentication_required: 'Войдите для продолжения.',
  validation_failed: 'Проверьте введённые значения.',
  sandbox_unavailable: 'Локальный sandbox не включён. Управление сервером недоступно.',
  drift_conflict: 'Обнаружены внешние изменения конфигурации. Проверьте drift перед применением.',
  pending_changes_require_cancel: 'Есть ожидающие изменения клиентов. Откройте входящее, просмотрите их и подтвердите отмену перед повторным импортом.',
  pending_preview_changed: 'Состояние изменилось после предварительного просмотра. Откройте список ожидающих изменений заново.',
  pending_cancel_unsafe: 'Учётные данные ожидающего клиента уже присутствуют в файле. Автоматическая отмена заблокирована; проверьте входящее и журнал операции.',
  management_unavailable: 'Агент управления недоступен. Проверьте состояние службы TunnelUI и повторите после восстановления связи.',
  client_still_applied: 'Учётные данные клиента ещё присутствуют в TrustTunnel. Запись сохранена; проверьте входящее и журнал операции.',
  inbound_not_managed: 'Входящее больше не находится под управлением. Доступ не изменён.',
  operation_in_progress: 'Для этого входящего уже выполняется операция. Обновите состояние и повторите позже.',
  empty_credentials_unsupported_v1_1_0: 'Нельзя отключить последнего активного клиента: TrustTunnel v1.1.0 не принимает пустой credentials.toml.',
  attachment_already_exists: 'Клиент уже привязан к этому входящему.',
  attachment_not_found: 'Привязка больше не существует. Обновите список.',
  adoption_preview_expired: 'Preview устарел. Выполните проверку конфигурации ещё раз.',
  inbound_already_adopted: 'Это входящее уже импортировано.',
  adoption_username_conflict: 'Username из конфигурации уже существует. Автоматическое объединение запрещено.',
  reimport_mapping_required: 'Набор клиентов изменился. Требуется явное сопоставление; автоматический re-import остановлен.',
  active_profile_not_found: 'У клиента нет активного применённого профиля TrustTunnel.',
  apply_failed: 'Применение не завершено. Предыдущая конфигурация восстановлена.',
  recovery_required: 'Откат не завершён. Новые изменения заблокированы до восстановления.',
  idempotency_conflict: 'Повторный запрос не совпадает с исходной операцией.',
  operation_failed: 'Операция завершилась ошибкой. Проверьте состояние сервиса и журнал операции.',
  unsupported_endpoint_version: 'Для изменения конфигурации требуется подтверждённая версия TrustTunnel 1.1.0.',
  unsupported_config_field: 'Это поле не поддерживается безопасным редактором TrustTunnel.',
  invalid_config_value: 'Значение настройки вне допустимого диапазона.',
  unsupported_config_value: 'Текущее значение настройки не удалось прочитать.',
  unsupported_rules_structure: 'Формат rules.toml содержит неподдерживаемые поля. Изменение заблокировано.',
  invalid_rules: 'Проверьте CIDR, hex-префикс, маску и действие правила.',
  no_config_changes: 'Изменений для применения нет.',
  config_preview_changed: 'Конфигурация изменилась после просмотра. Просмотрите изменения ещё раз.',
  too_many_rules: 'Превышено допустимое количество правил.',
  profile_pending_apply: 'Профиль изменяется или конфигурация не подтверждена. Проверьте входящее и журнал операции.',
  expiry_preview_changed: 'Список затрагиваемых клиентов изменился. Проверьте предварительный просмотр заново.',
  expiry_retry_unavailable: 'Повтор для этой записи сейчас недоступен.',
  restart_failed: 'Служба не перезапустилась. Проверьте журнал операции и состояние входящего.',
  health_failed: 'После перезапуска проверка здоровья не прошла. Проверьте rollback и входящее.',
  rollback_failed: 'Откат не подтверждён. Требуется восстановление входящего.',
};
export function errorText(error: unknown): string {
  return error instanceof ApiError ? messages[error.code] ?? 'Запрос не выполнен. Обновите данные и повторите.' : 'Нет связи с панелью. Проверьте подключение и повторите.';
}
