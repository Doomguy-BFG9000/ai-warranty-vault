import { Stack, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useRef } from 'react';
import { ActivityIndicator, FlatList, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Banner } from '@/components/Banner';
import { EmptyState } from '@/components/EmptyState';
import { describeStatus, useDocumentChunks, useProductDocument } from '@/hooks/useProductDocuments';
import { getAiErrorMessage } from '@/lib/aiApi';
import { colors, radii, spacing, typography } from '@/theme';
import type { DocumentChunk } from '@/types/ai';

/**
 * Where a citation leads.
 *
 * It shows the indexed passages, not a rendered PDF: the passages are what the answer
 * was actually built from, so this is the text to check an answer against. Arriving
 * from a citation highlights and scrolls to that exact passage — the point of a
 * citation is being able to see the sentence, not just the page number.
 */
export default function DocumentScreen() {
  const { id, chunk: highlightedChunkId } = useLocalSearchParams<{
    id: string;
    chunk?: string;
    page?: string;
  }>();

  const { data: document, isLoading, isError, error } = useProductDocument(id);
  const { data: chunks, isLoading: loadingChunks } = useDocumentChunks(id);
  const listRef = useRef<FlatList<DocumentChunk>>(null);

  const highlightIndex = useMemo(() => {
    if (!highlightedChunkId || !chunks) return -1;
    return chunks.findIndex((item) => item.chunk_id === highlightedChunkId);
  }, [chunks, highlightedChunkId]);

  useEffect(() => {
    if (highlightIndex < 0) return;
    const timer = setTimeout(
      () => listRef.current?.scrollToIndex({ index: highlightIndex, animated: true, viewPosition: 0.2 }),
      120,
    );
    return () => clearTimeout(timer);
  }, [highlightIndex]);

  if (isLoading) {
    return (
      <SafeAreaView style={styles.centered} edges={['bottom']}>
        <Stack.Screen options={{ title: 'Source' }} />
        <ActivityIndicator color={colors.primary} size="large" />
      </SafeAreaView>
    );
  }

  if (isError || !document) {
    return (
      <SafeAreaView style={styles.flex} edges={['bottom']}>
        <Stack.Screen options={{ title: 'Source' }} />
        <EmptyState emoji="🔍" title="This document is not available" message={getAiErrorMessage(error)} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.flex} edges={['bottom']}>
      <Stack.Screen options={{ title: document.title, headerBackTitle: 'Back' }} />

      <FlatList<DocumentChunk>
        ref={listRef}
        data={chunks ?? []}
        keyExtractor={(item) => item.chunk_id}
        contentContainerStyle={styles.list}
        // Passages vary a lot in height, so an exact offset is not known up front.
        // Scrolling to a rough offset and letting the list correct itself is more
        // robust than guessing an item height.
        onScrollToIndexFailed={(info) => {
          listRef.current?.scrollToOffset({ offset: info.averageItemLength * info.index, animated: false });
          setTimeout(
            () => listRef.current?.scrollToIndex({ index: info.index, animated: true, viewPosition: 0.2 }),
            60,
          );
        }}
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.meta}>
              {[
                describeStatus(document),
                document.page_count ? `${document.page_count} pages` : null,
                document.extraction_method === 'vision_ocr'
                  ? 'read by a vision model'
                  : document.extraction_method === 'plain_text'
                    ? 'plain text'
                    : document.extraction_method === 'pdf_text'
                      ? 'PDF text layer'
                      : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </Text>
            {document.notice ? (
              <Banner
                tone={document.status === 'failed' ? 'danger' : 'warning'}
                title={document.status === 'failed' ? 'This document could not be read' : 'Partly indexed'}
                message={document.notice}
              />
            ) : null}
            {document.stale_index ? (
              <Banner
                tone="warning"
                title="Indexed with an older model"
                message="Its passages are excluded from search until it is reindexed."
              />
            ) : null}
            <Text style={styles.sectionLabel}>
              Indexed passages — this is the text answers are built from
            </Text>
          </View>
        }
        renderItem={({ item }) => {
          const highlighted = item.chunk_id === highlightedChunkId;
          return (
            <View style={[styles.chunk, highlighted && styles.chunkHighlighted]}>
              <View style={styles.chunkHeader}>
                <Text style={[styles.locator, highlighted && styles.locatorHighlighted]}>
                  {item.locator}
                </Text>
                {highlighted ? <Text style={styles.citedBadge}>Cited</Text> : null}
              </View>
              <Text style={styles.chunkText}>{item.content}</Text>
            </View>
          );
        }}
        ListEmptyComponent={
          loadingChunks ? (
            <ActivityIndicator color={colors.primary} />
          ) : (
            <EmptyState
              emoji="📄"
              title="No indexed passages"
              message={
                document.status === 'failed'
                  ? 'Nothing could be read from this file. Retry it, or replace it with a clearer scan.'
                  : 'Indexing has not finished yet.'
              }
            />
          )
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  centered: {
    flex: 1,
    backgroundColor: colors.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
  list: { padding: spacing.lg, gap: spacing.md },
  header: { gap: spacing.md },
  meta: { ...typography.caption, color: colors.textMuted },
  sectionLabel: { ...typography.label, color: colors.textMuted, marginTop: spacing.xs },
  chunk: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  chunkHighlighted: { borderColor: colors.primary, backgroundColor: colors.surfaceAlt },
  chunkHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  locator: { ...typography.caption, color: colors.textFaint },
  locatorHighlighted: { color: colors.primary },
  citedBadge: { ...typography.caption, color: colors.primary, fontWeight: '600' },
  chunkText: { ...typography.body, color: colors.text, lineHeight: 21 },
});
