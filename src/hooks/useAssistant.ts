import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { aiRequest } from '@/lib/aiApi';
import { env } from '@/lib/env';
import type { ThreadState, TurnResponse } from '@/types/ai';

export const assistantKeys = {
  all: ['assistant'] as const,
  threads: (productId?: string) => [...assistantKeys.all, 'threads', productId ?? 'any'] as const,
  thread: (threadId: string) => [...assistantKeys.all, 'thread', threadId] as const,
};

/**
 * The conversation, as the backend sees it.
 *
 * This is the whole of the "close the app and come back" story: the transcript and
 * whatever the assistant is still waiting for both come from the server, so a cold
 * start resumes mid-flow with nothing held in app memory.
 */
export function useThread(threadId: string | undefined) {
  return useQuery({
    queryKey: assistantKeys.thread(threadId ?? 'none'),
    enabled: !!threadId && env.hasAiApi,
    queryFn: () => aiRequest<ThreadState>(`/v1/threads/${threadId}`),
    // A pending question or draft is durable server state, not something to cache.
    staleTime: 0,
  });
}

export function useThreads(productId?: string) {
  return useQuery({
    queryKey: assistantKeys.threads(productId),
    enabled: env.hasAiApi,
    queryFn: () =>
      aiRequest<ThreadState[]>('/v1/threads', { query: { product_id: productId, limit: 20 } }),
  });
}

export function useCreateThread() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (productId?: string) =>
      aiRequest<ThreadState>('/v1/threads', {
        method: 'POST',
        body: { product_id: productId ?? null },
      }),
    onSuccess: (thread) => {
      queryClient.setQueryData(assistantKeys.thread(thread.thread_id), thread);
      void queryClient.invalidateQueries({ queryKey: assistantKeys.all });
    },
  });
}

/**
 * Sends a turn. The thread id is a mutation variable rather than a hook argument so a
 * caller can create a thread and post to it in one go — on the first message the
 * thread does not exist yet when the hook is built.
 */
export function useSendMessage() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ threadId, content }: { threadId: string; content: string }) =>
      aiRequest<TurnResponse>(`/v1/threads/${threadId}/messages`, {
        method: 'POST',
        body: { content },
      }),
    onSuccess: (turn) => {
      // Refetch rather than patch: one turn may have appended two messages, changed
      // what the thread is waiting for, and created a draft. The server's view is the
      // one that matters.
      void queryClient.invalidateQueries({ queryKey: assistantKeys.thread(turn.thread_id) });
      void queryClient.invalidateQueries({ queryKey: assistantKeys.all });
    },
  });
}

export interface ResumeInput {
  /** Reply to a clarifying question. */
  answer?: string;
  /** Decide on a pending draft. `subject`/`body` carry the user's edits. */
  decision?: 'approve' | 'reject';
  subject?: string;
  body?: string;
}

/**
 * Answers whatever the conversation is waiting for.
 *
 * Safe to call twice. A repeated approval is reported as `applied: false` with a
 * reason, and the stored approval is not rewritten — the service enforces that at the
 * database level, so a double-tapped button cannot produce two approvals.
 */
export function useResumeThread(threadId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: ResumeInput) =>
      aiRequest<TurnResponse>(`/v1/threads/${threadId}/resume`, {
        method: 'POST',
        body: {
          answer: input.answer ?? null,
          decision: input.decision ?? null,
          subject: input.subject ?? null,
          body: input.body ?? null,
        },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: assistantKeys.thread(threadId ?? 'none') });
      void queryClient.invalidateQueries({ queryKey: assistantKeys.all });
    },
  });
}
