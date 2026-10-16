import { describe, expect, it } from 'vitest';
import type { Attachment, Client } from '../../shared/types';
import { clientStatus } from './clientStatus';

const attachment = {
  applied_state: 'active', sync_state: 'active', applied_expired: false,
} as Attachment;
const base = {
  enabled: true, expires_at: null, expiry_managed: true,
  attachments: [attachment],
} as Client;

describe('clientStatus', () => {
  it('does not call overdue legacy access revoked', () => {
    expect(clientStatus({ ...base, expires_at: 90, expiry_managed: false }, 100).label)
      .toBe('Срок истёк · не применён');
  });
  it('distinguishes an overdue active credential from confirmed revocation', () => {
    expect(clientStatus({ ...base, expires_at: 90 }, 100).label).toBe('Ожидает отключения');
    expect(clientStatus({ ...base, expires_at: 90, attachments: [
      { ...attachment, applied_expired: true },
    ] }, 100).label).toBe('Срок истёк · отозван');
  });
  it('shows approaching expiry and apply errors', () => {
    expect(clientStatus({ ...base, expires_at: 100 + 86400 }, 100).label)
      .toBe('Скоро истечёт');
    expect(clientStatus({ ...base, attachments: [
      { ...attachment, sync_state: 'error' },
    ] }, 100).label).toBe('Ошибка применения');
  });
});
