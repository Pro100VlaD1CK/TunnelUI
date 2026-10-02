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
});
