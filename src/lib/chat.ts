import type { AssistantMessage } from '@/types/ai';

/**
 * The transcript as it should be rendered, including the turn that is still in flight.
 *
 * A turn takes seconds — retrieval, then a model call — and the server transcript is
 * the source of truth for it. Without an optimistic entry the message disappears from
 * the input box and reappears only once the answer arrives, which reads as the app
 * having dropped it.
 *
 * The optimistic entry is *hidden* once the server's own copy shows up, rather than
 * cleared on success, so there is no frame in which neither is on screen. That makes
 * this a pure function of (transcript, what was sent) and keeps it out of an effect.
 */
export const OPTIMISTIC_MESSAGE_ID = 'optimistic-user-message';

export function withOptimisticMessage(
  messages: AssistantMessage[],
  sent: string | null,
): AssistantMessage[] {
  if (!sent) return messages;

  // Compare against the most recent *user* turn only: an assistant message echoing
  // the question back would otherwise hide the bubble early.
  const lastUserMessage = [...messages].reverse().find((message) => message.role === 'user');
  if (lastUserMessage?.content === sent) return messages;

  return [
    ...messages,
    {
      id: OPTIMISTIC_MESSAGE_ID,
      role: 'user',
      content: sent,
      citations: [],
      outcome: null,
      used_product_facts: false,
      created_at: new Date().toISOString(),
    },
  ];
}

/** True while the bubble on screen has no counterpart on the server yet. */
export function isAwaitingDelivery(messages: AssistantMessage[], sent: string | null): boolean {
  return withOptimisticMessage(messages, sent).length > messages.length;
}
