import { StyleSheet, Text, View } from 'react-native';

import { CitationList } from '@/components/CitationList';
import { colors, radii, spacing, typography } from '@/theme';
import type { AssistantMessage, Citation } from '@/types/ai';

/**
 * One turn in the conversation.
 *
 * An assistant turn carries its outcome as a visible label, not just as tone of voice.
 * "The documents do not say" is a legitimate result of a working system, and reading
 * it as a failure is the mistake this label exists to prevent.
 */
export function ChatBubble({
  message,
  onOpenCitation,
}: {
  message: AssistantMessage;
  onOpenCitation: (citation: Citation) => void;
}) {
  const isUser = message.role === 'user';
  const badge = outcomeBadge(message);

  return (
    <View style={[styles.row, isUser ? styles.rowUser : styles.rowAssistant]}>
      <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAssistant]}>
        {badge ? (
          <Text style={[styles.badge, { color: badge.color }]}>{badge.label}</Text>
        ) : null}
        <Text style={[styles.text, isUser && styles.textUser]}>{message.content}</Text>
        {!isUser ? (
          <CitationList citations={message.citations} onOpen={onOpenCitation} />
        ) : null}
      </View>
    </View>
  );
}

function outcomeBadge(message: AssistantMessage): { label: string; color: string } | null {
  if (message.role !== 'assistant') return null;
  switch (message.outcome) {
    case 'no_answer':
      return { label: 'Not in the documents', color: colors.warning };
    case 'conflicting_sources':
      return { label: 'Sources disagree', color: colors.warning };
    case 'clarification_needed':
      return { label: 'Needs a detail', color: colors.primary };
    case 'draft_ready':
      return { label: 'Draft ready for review', color: colors.primary };
    case 'draft_approved':
      return { label: 'Approved and saved', color: colors.success };
    case 'draft_rejected':
      return { label: 'Discarded', color: colors.textFaint };
    case 'error':
      return { label: 'Something went wrong', color: colors.danger };
    default:
      return null;
  }
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row' },
  rowUser: { justifyContent: 'flex-end' },
  rowAssistant: { justifyContent: 'flex-start' },
  bubble: {
    maxWidth: '88%',
    borderRadius: radii.lg,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    gap: spacing.xs,
  },
  bubbleUser: { backgroundColor: colors.primary, borderBottomRightRadius: radii.sm },
  bubbleAssistant: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderBottomLeftRadius: radii.sm,
  },
  badge: { ...typography.caption, fontWeight: '600', textTransform: 'uppercase' },
  text: { ...typography.body, color: colors.text, lineHeight: 21 },
  textUser: { color: colors.onPrimary },
});
