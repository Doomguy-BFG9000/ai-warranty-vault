import { AiApiError, AiApiNotConfiguredError, getAiErrorMessage } from '@/lib/aiApi';

// The client imports the Supabase client to read the session token, which drags in
// AsyncStorage's native module at import time. Only the error mapping is under test
// here, so that surface is stubbed away. (babel-jest hoists jest.mock above the
// imports, so the position below is cosmetic.)
jest.mock('@/lib/supabase', () => ({
  supabase: { auth: { getSession: jest.fn() } },
}));

/**
 * The client's job on failure is to turn three different FastAPI error shapes into one
 * thing a screen can branch on. These tests pin that mapping, because getting it wrong
 * shows up as a screen displaying `[object Object]` to a user.
 */
describe('AiApiError', () => {
  it('fuses "not yours" and "does not exist" into one condition', () => {
    // The service answers 404 for both on purpose — distinguishing them would tell a
    // caller whether an id exists in someone else's account.
    const error = new AiApiError('No such document.', 404, null, 'op-1');
    expect(error.isNotFound).toBe(true);
    expect(error.isProviderDown).toBe(false);
  });

  it('marks a provider outage as retryable rather than as a normal failure', () => {
    const error = new AiApiError('unavailable', 503, 'provider_error', null);
    expect(error.isProviderDown).toBe(true);
    expect(getAiErrorMessage(error)).toMatch(/unavailable right now/i);
  });

  it('keeps the operation id so a report can be traced to the service log', () => {
    const error = new AiApiError('boom', 500, null, 'abc-123');
    expect(error.operationId).toBe('abc-123');
  });
});

describe('getAiErrorMessage', () => {
  it('asks for a session again on 401 rather than showing the raw detail', () => {
    expect(getAiErrorMessage(new AiApiError('Invalid access token.', 401, null, null))).toMatch(
      /sign in again/i,
    );
  });

  it('passes through a specific message the service chose to send', () => {
    const message = 'That file is larger than the 15 MB limit.';
    expect(getAiErrorMessage(new AiApiError(message, 400, null, null))).toBe(message);
  });

  it('explains the missing configuration instead of a generic failure', () => {
    expect(getAiErrorMessage(new AiApiNotConfiguredError())).toMatch(/EXPO_PUBLIC_AI_API_URL/);
  });

  it('falls back to something sayable for an unknown throw', () => {
    expect(getAiErrorMessage(new TypeError('undefined is not a function'))).toBe(
      'Something went wrong. Please try again.',
    );
    expect(getAiErrorMessage('a bare string')).toBe('Something went wrong. Please try again.');
  });
});
