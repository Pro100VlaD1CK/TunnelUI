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
  attached_client_requires_apply: 'У клиента есть привязки. Сначала требуется безопасное применение конфигурации; оно ещё не подключено.',
  csrf_failed: 'Защитный токен устарел. Повторите действие.',
  session_expired: 'Сессия истекла. Войдите снова.',
  authentication_required: 'Войдите для продолжения.',
  validation_failed: 'Проверьте введённые значения.',
};
export function errorText(error: unknown): string {
  return error instanceof ApiError ? messages[error.code] ?? 'Запрос не выполнен. Обновите данные и повторите.' : 'Нет связи с панелью. Проверьте подключение и повторите.';
}
