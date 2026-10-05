import { describe, expect, it } from 'vitest';
import { ApiError, errorText } from './api';

describe('safe user-facing error messages', () => {
  it('does not expose server strings or raw network errors', () => {
    expect(errorText(new ApiError('token=secret', 500))).not.toContain('secret');
    expect(errorText(new Error('password=secret'))).not.toContain('secret');
  });
  it('explains optimistic concurrency conflicts', () => {
    expect(errorText(new ApiError('revision_conflict', 409))).toContain('Обновите список');
  });
  it('explains apply locking, last-client protection and recovery', () => {
    expect(errorText(new ApiError('operation_in_progress', 409))).toContain('уже выполняется');
    expect(errorText(new ApiError('empty_credentials_unsupported_v1_1_0', 409))).toContain('последнего');
    expect(errorText(new ApiError('recovery_required', 409))).toContain('восстановления');
  });
});
