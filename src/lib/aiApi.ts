import { env } from '@/lib/env';
import { supabase } from '@/lib/supabase';

/**
 * Thin client for the document-assistant service.
 *
 * Every request carries the current Supabase access token. That token is the only
 * thing that identifies the caller: the service verifies its signature and then runs
 * its queries as that user, so there is no user id in any request body here — and
 * nothing for this file to get wrong about whose data is being asked for.
 *
 * Errors are normalised into `AiApiError`, which keeps the HTTP status and the
 * service's own error code. Screens branch on those rather than on message text.
 */

export class AiApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  /** Correlates with the service's logs, so a bug report can be traced. */
  readonly operationId: string | null;

  constructor(message: string, status: number, code: string | null, operationId: string | null) {
    super(message);
    this.name = 'AiApiError';
    this.status = status;
    this.code = code;
    this.operationId = operationId;
  }

  /** True when the resource does not exist, or is not this user's. The two are fused. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** The AI provider is unreachable. Worth a retry; not worth a fabricated answer. */
  get isProviderDown(): boolean {
    return this.status === 503;
  }
}

export class AiApiNotConfiguredError extends Error {
  constructor() {
    super(
      'The document assistant is not configured. Set EXPO_PUBLIC_AI_API_URL in .env ' +
        'and restart the dev server.',
    );
    this.name = 'AiApiNotConfiguredError';
  }
}

/** Long enough for a model call with retrieval in front of it, short enough to fail. */
const DEFAULT_TIMEOUT_MS = 90_000;

interface RequestOptions {
  method?: 'GET' | 'POST' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  timeoutMs?: number;
}

export async function aiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!env.hasAiApi) throw new AiApiNotConfiguredError();

  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session) {
    throw new AiApiError('You need to be signed in.', 401, 'no_session', null);
  }

  const url = new URL(`${env.aiApiUrl}${path}`);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  // AbortController rather than a racing promise, so a timed-out request is actually
  // cancelled instead of continuing in the background.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method: options.method ?? 'GET',
      headers: {
        Authorization: `Bearer ${data.session.access_token}`,
        ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });
  } catch (cause) {
    const aborted = cause instanceof Error && cause.name === 'AbortError';
    throw new AiApiError(
      aborted
        ? 'That took too long. Please try again.'
        : 'Could not reach the document assistant. Check that it is running.',
      0,
      aborted ? 'timeout' : 'network',
      null,
    );
  } finally {
    clearTimeout(timer);
  }

  const operationId = response.headers.get('X-Operation-Id');

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    throw new AiApiError(
      messageFrom(payload) ?? `Request failed (${response.status}).`,
      response.status,
      codeFrom(payload),
      operationId,
    );
  }

  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

/**
 * FastAPI puts errors under `detail`, which is a string for simple raises and an
 * object for the ones that carry a machine-readable code. Validation errors arrive as
 * a list. All three shapes are unwrapped here so screens never parse error bodies.
 */
function messageFrom(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string') return message;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: unknown };
    if (typeof first?.msg === 'string') return first.msg;
  }
  return null;
}

function codeFrom(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const code = (detail as { code?: unknown }).code;
    if (typeof code === 'string') return code;
  }
  return null;
}

/** A user-facing sentence for any failure from this client. */
export function getAiErrorMessage(error: unknown): string {
  if (error instanceof AiApiNotConfiguredError) return error.message;
  if (error instanceof AiApiError) {
    if (error.isProviderDown) {
      return 'The AI service is unavailable right now. Please try again in a moment.';
    }
    if (error.status === 401) return 'Your session has expired. Please sign in again.';
    return error.message;
  }
  return 'Something went wrong. Please try again.';
}
