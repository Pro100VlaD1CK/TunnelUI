import type { Client } from '../../shared/types';

export function clientStatus(client: Client, nowSeconds: number): {
  label: string; color?: string;
} {
  if (!client.enabled) return { label: 'Отключён' };
  const live = client.attachments.filter(item => item.applied_state === 'active');
  if (live.some(item => item.sync_state === 'error' || item.sync_state === 'conflict')) {
    return { label: 'Ошибка применения', color: 'error' };
  }
  if (client.expires_at !== null && client.expires_at <= nowSeconds) {
    if (!client.expiry_managed) return { label: 'Срок истёк · не применён', color: 'warning' };
    if (live.some(item => !item.applied_expired)) {
      return { label: 'Ожидает отключения', color: 'warning' };
    }
    return { label: live.length ? 'Срок истёк · отозван' : 'Срок истёк', color: 'warning' };
  }
  if (live.some(item => item.sync_state === 'pending')) {
    return { label: 'Ожидает применения', color: 'processing' };
  }
  if (client.expires_at !== null && client.expires_at <= nowSeconds + 7 * 86400) {
    return { label: 'Скоро истечёт', color: 'warning' };
  }
  return { label: 'Включён', color: 'success' };
}
