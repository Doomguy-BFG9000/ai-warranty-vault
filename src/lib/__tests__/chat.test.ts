import { OPTIMISTIC_MESSAGE_ID, isAwaitingDelivery, withOptimisticMessage } from '@/lib/chat';
import type { AssistantMessage } from '@/types/ai';

/**
 * The transcript is server-owned, but a turn takes seconds. These tests pin the one
 * rule that makes an optimistic entry safe: it appears at once, and it goes away only
 * when the server's own copy of that same turn has arrived — never on a timer and
 * never on "the request succeeded", both of which leave a frame with nothing on
 * screen.
 */
function message(overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return {
    id: 'm1',
    role: 'user',
    content: 'Co oznacza kod E17?',
    citations: [],
    outcome: null,
    used_product_facts: false,
    created_at: '2026-09-29T10:00:00Z',
    ...overrides,
  };
}

describe('withOptimisticMessage', () => {
  it('shows what was just sent, before the server has it', () => {
    const rendered = withOptimisticMessage([], 'Jak wyczyścić filtr?');
    expect(rendered).toHaveLength(1);
    expect(rendered[0].content).toBe('Jak wyczyścić filtr?');
    expect(rendered[0].role).toBe('user');
    expect(rendered[0].id).toBe(OPTIMISTIC_MESSAGE_ID);
  });

  it('appends after the existing transcript rather than replacing it', () => {
    const history = [message({ id: 'a' }), message({ id: 'b', role: 'assistant' })];
    const rendered = withOptimisticMessage(history, 'Kolejne pytanie');
    expect(rendered.map((m) => m.id)).toEqual(['a', 'b', OPTIMISTIC_MESSAGE_ID]);
  });

  it('hides the optimistic entry once the server transcript contains that turn', () => {
    const delivered = [message({ id: 'server', content: 'Jak wyczyścić filtr?' })];
    expect(withOptimisticMessage(delivered, 'Jak wyczyścić filtr?')).toEqual(delivered);
  });

  it('keeps showing it while the transcript still lacks it', () => {
    // A refetch that has not caught up yet — the previous turn is there, this one is not.
    const stale = [message({ id: 'older', content: 'Poprzednie pytanie' })];
    expect(withOptimisticMessage(stale, 'Nowe pytanie')).toHaveLength(2);
  });

  it('compares against the last user turn, not the last message', () => {
    // An assistant turn quoting the question back must not make the bubble vanish
    // before the user's own turn has been stored.
    const history = [
      message({ id: 'assistant', role: 'assistant', content: 'Jak wyczyścić filtr?' }),
    ];
    expect(withOptimisticMessage(history, 'Jak wyczyścić filtr?')).toHaveLength(2);
  });

  it('renders nothing extra when nothing is in flight', () => {
    const history = [message()];
    expect(withOptimisticMessage(history, null)).toEqual(history);
  });

  it('never marks an optimistic entry as grounded in anything', () => {
    const [optimistic] = withOptimisticMessage([], 'x');
    expect(optimistic.citations).toEqual([]);
    expect(optimistic.used_product_facts).toBe(false);
    expect(optimistic.outcome).toBeNull();
  });
});

describe('isAwaitingDelivery', () => {
  it('is true only while the sent turn has no server counterpart', () => {
    expect(isAwaitingDelivery([], 'pytanie')).toBe(true);
    expect(isAwaitingDelivery([message({ content: 'pytanie' })], 'pytanie')).toBe(false);
    expect(isAwaitingDelivery([message()], null)).toBe(false);
  });
});
