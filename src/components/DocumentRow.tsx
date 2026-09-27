import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { describeStatus, isIndexing } from '@/hooks/useProductDocuments';
import { colors, radii, spacing, typography } from '@/theme';
import type { DocumentKind, ProductDocument } from '@/types/ai';

const KIND_LABELS: Record<DocumentKind, string> = {
  manual: 'Manual',
  warranty_card: 'Warranty card',
  service_terms: 'Service terms',
  other: 'Document',
};

const KIND_EMOJI: Record<DocumentKind, string> = {
  manual: '📘',
  warranty_card: '🧾',
  service_terms: '📄',
  other: '📎',
};

/**
 * One attached document, with its indexing state in plain words.
 *
 * Progress is shown rather than hidden, because a document that is still being read
 * genuinely cannot answer questions yet — and a failure offers a retry instead of
 * leaving a spinner running forever.
 */
export function DocumentRow({
  document,
  onPress,
  onRetry,
  onDelete,
  busy = false,
}: {
  document: ProductDocument;
  onPress: () => void;
  onRetry: () => void;
  onDelete: () => void;
  busy?: boolean;
}) {
  const working = isIndexing(document);
  const failed = document.status === 'failed';
  const canRetry = failed || document.stale_index;

  return (
    <View style={styles.card}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${document.title}, ${describeStatus(document)}`}
        onPress={onPress}
        style={({ pressed }) => [styles.main, pressed && styles.pressed]}
      >
        <Text style={styles.emoji}>{KIND_EMOJI[document.kind]}</Text>
        <View style={styles.body}>
          <Text style={styles.title} numberOfLines={2}>
            {document.title}
          </Text>
          <Text style={styles.meta} numberOfLines={1}>
            {[
              KIND_LABELS[document.kind],
              document.page_count ? `${document.page_count} pages` : null,
              document.extraction_method === 'vision_ocr' ? 'read by vision' : null,
            ]
              .filter(Boolean)
              .join(' · ')}
          </Text>
          <View style={styles.statusRow}>
            {working ? <ActivityIndicator size="small" color={colors.textMuted} /> : null}
            <Text
              style={[
                styles.status,
                failed && styles.statusFailed,
                document.status === 'ready' && !document.stale_index && styles.statusReady,
                document.stale_index && styles.statusStale,
              ]}
            >
              {describeStatus(document)}
            </Text>
          </View>
          {document.notice ? (
            <Text style={styles.notice} numberOfLines={3}>
              {document.notice}
            </Text>
          ) : null}
        </View>
      </Pressable>

      <View style={styles.actions}>
        {canRetry ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Retry indexing ${document.title}`}
            disabled={busy || working}
            onPress={onRetry}
            style={({ pressed }) => [styles.action, pressed && styles.pressed]}
          >
            <Text style={styles.actionLabel}>Retry</Text>
          </Pressable>
        ) : null}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Remove ${document.title}`}
          disabled={busy}
          onPress={onDelete}
          style={({ pressed }) => [styles.action, pressed && styles.pressed]}
        >
          <Text style={[styles.actionLabel, styles.actionDanger]}>Remove</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: 'hidden',
  },
  main: { flexDirection: 'row', gap: spacing.md, padding: spacing.lg },
  pressed: { opacity: 0.7 },
  emoji: { fontSize: 24 },
  body: { flex: 1, gap: spacing.xs },
  title: { ...typography.subheading, color: colors.text },
  meta: { ...typography.caption, color: colors.textMuted },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginTop: spacing.xs },
  status: { ...typography.caption, color: colors.textMuted },
  statusReady: { color: colors.success },
  statusFailed: { color: colors.danger },
  statusStale: { color: colors.warning },
  notice: { ...typography.caption, color: colors.textFaint, lineHeight: 17 },
  actions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: spacing.lg,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
  },
  action: { paddingVertical: spacing.xs, paddingHorizontal: spacing.xs },
  actionLabel: { ...typography.label, color: colors.primary },
  actionDanger: { color: colors.danger },
});
