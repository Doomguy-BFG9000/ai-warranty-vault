import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radii, spacing, typography } from '@/theme';
import type { Citation } from '@/types/ai';

/**
 * The sources behind an answer, each one openable.
 *
 * Every field shown here was produced by the server from its own database row — the
 * model only ever returned a label like "S2". So a citation cannot point at a page
 * that does not exist, a document the user does not own, or a passage the answer was
 * not actually built from.
 */
export function CitationList({
  citations,
  onOpen,
}: {
  citations: Citation[];
  onOpen: (citation: Citation) => void;
}) {
  if (citations.length === 0) return null;

  return (
    <View style={styles.container}>
      <Text style={styles.heading}>
        {citations.length === 1 ? 'Source' : `Sources (${citations.length})`}
      </Text>
      <View style={styles.chips}>
        {citations.map((citation) => (
          <Pressable
            key={citation.chunk_id}
            accessibilityRole="button"
            accessibilityLabel={`Open ${citation.document_title ?? 'source'}, ${citation.locator}`}
            onPress={() => onOpen(citation)}
            style={({ pressed }) => [styles.chip, pressed && styles.pressed]}
          >
            <Text style={styles.chipTitle} numberOfLines={1}>
              {citation.document_title ?? 'Document'}
            </Text>
            <Text style={styles.chipLocator}>{citation.locator}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: spacing.xs, marginTop: spacing.sm },
  heading: { ...typography.caption, color: colors.textFaint, textTransform: 'uppercase' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    maxWidth: '100%',
  },
  pressed: { opacity: 0.7 },
  chipTitle: { ...typography.caption, color: colors.text, flexShrink: 1 },
  chipLocator: { ...typography.caption, color: colors.primary },
});
