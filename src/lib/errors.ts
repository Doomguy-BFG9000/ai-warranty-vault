import { AuthError, PostgrestError } from '@supabase/supabase-js';

/**
 * Turns backend errors into something worth showing a user.
 *
 * Supabase surfaces terse, implementation-flavoured messages ("Invalid login
 * credentials", "duplicate key value violates unique constraint"). Showing them
 * raw is both confusing and needlessly revealing, so known cases are mapped and
 * anything unrecognised falls back to a generic message.
 */

/**
 * Raised by the enforce_free_tier_limit() trigger. PostgREST maps the PT402
 * SQLSTATE to HTTP 402; the message prefix is matched as well so detection
 * survives any change to that mapping.
 */
export const FREE_TIER_ERROR_MARKER = 'FREE_TIER_LIMIT_REACHED';

export function isFreeTierLimitError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const message = 'message' in error ? String(error.message) : '';
  const code = 'code' in error ? String(error.code) : '';
  return message.includes(FREE_TIER_ERROR_MARKER) || code === 'PT402';
}

const AUTH_MESSAGES: Record<string, string> = {
  'invalid login credentials': 'That email or password is incorrect.',
  'email not confirmed': 'Please confirm your email address before signing in.',
  'user already registered': 'An account with that email already exists.',
  'password should be at least 6 characters':
    'Please choose a password of at least 6 characters.',
  'unable to validate email address: invalid format':
    'Please enter a valid email address.',
  'email rate limit exceeded': 'Too many attempts. Please wait a moment and try again.',
  'over_email_send_rate_limit':
    'Too many attempts. Please wait a moment and try again.',
};

export function getAuthErrorMessage(error: unknown): string {
  if (error instanceof AuthError || isErrorLike(error)) {
    const raw = String((error as { message: string }).message).toLowerCase();

    for (const [needle, friendly] of Object.entries(AUTH_MESSAGES)) {
      if (raw.includes(needle)) return friendly;
    }

    // Network failures deserve a distinct, actionable message.
    if (raw.includes('network') || raw.includes('fetch')) {
      return 'Could not reach the server. Check your connection and try again.';
    }
  }

  return 'Something went wrong. Please try again.';
}

export function getDatabaseErrorMessage(error: unknown): string {
  if (isFreeTierLimitError(error)) {
    return 'You have reached the free plan limit. Upgrade for unlimited storage.';
  }

  if (error instanceof PostgrestError || isErrorLike(error)) {
    const raw = String((error as { message: string }).message).toLowerCase();
    if (raw.includes('row-level security')) {
      return 'You do not have permission to do that.';
    }
    if (raw.includes('network') || raw.includes('fetch')) {
      return 'Could not reach the server. Check your connection and try again.';
    }
  }

  return 'Something went wrong. Please try again.';
}

function isErrorLike(error: unknown): error is { message: string } {
  return (
    typeof error === 'object' &&
    error !== null &&
    'message' in error &&
    typeof (error as { message: unknown }).message === 'string'
  );
}
