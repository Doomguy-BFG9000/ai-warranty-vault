import { Stack, router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Alert, FlatList, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Banner } from '@/components/Banner';
import { Button } from '@/components/Button';
import { DocumentRow } from '@/components/DocumentRow';
import { EmptyState } from '@/components/EmptyState';
import { useProduct } from '@/hooks/useProducts';
import {
  useAddProductDocument,
  useDeleteDocument,
  useProductDocuments,
  useReindexDocument,
} from '@/hooks/useProductDocuments';
import { getAiErrorMessage } from '@/lib/aiApi';
import { pickDocument } from '@/lib/documents';
import { env } from '@/lib/env';
import { colors, spacing, typography } from '@/theme';
import type { DocumentKind, ProductDocument } from '@/types/ai';

const KIND_CHOICES: { kind: DocumentKind; label: string }[] = [
  { kind: 'manual', label: 'Manual' },
  { kind: 'warranty_card', label: 'Warranty card' },
  { kind: 'service_terms', label: 'Service terms' },
  { kind: 'other', label: 'Something else' },
];

/**
 * The documents attached to one product.
 *
 * The screen's job is to be honest about state: queued, reading, indexing, ready with
 * a passage count, or failed with a retry. A document that is still being read cannot
 * answer questions yet, and pretending otherwise would make the assistant look broken
 * when it is merely not finished.
 */
export default function ProductDocumentsScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: product } = useProduct(id);
  const { data: documents, isLoading, isError, error, refetch, isRefetching } =
    useProductDocuments(id);

  const addDocument = useAddProductDocument();
  const reindex = useReindexDocument();
  const remove = useDeleteDocument();
  const [busyId, setBusyId] = useState<string | null>(null);

  async function handleAdd() {
    try {
      const picked = await pickDocument();
      if (!picked) return;

      // Asked rather than guessed from the filename: the kind decides how the document
      // is labelled in a citation, and "warranty card" versus "manual" is exactly the
      // distinction a user cares about when they read where an answer came from.
      Alert.alert('What kind of document is this?', picked.name, [
        ...KIND_CHOICES.map((choice) => ({
          text: choice.label,
          onPress: () => {
            addDocument.mutate(
              { productId: id, title: picked.name, kind: choice.kind, picked },
              {
                onError: (err) => Alert.alert('Could not add that file', getAiErrorMessage(err)),
              },
            );
          },
        })),
        { text: 'Cancel', style: 'cancel' as const },
      ]);
    } catch (err) {
      Alert.alert('Could not use that file', err instanceof Error ? err.message : String(err));
    }
  }

  function handleRetry(document: ProductDocument) {
    setBusyId(document.id);
    reindex.mutate(document.id, {
      onError: (err) => Alert.alert('Could not retry', getAiErrorMessage(err)),
      onSettled: () => setBusyId(null),
    });
  }

  function handleDelete(document: ProductDocument) {
    Alert.alert(
      'Remove this document?',
      'The file and everything indexed from it will be deleted. Answers will no longer be able to cite it.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: () => {
            setBusyId(document.id);
            remove.mutate(document, {
              onError: (err) => Alert.alert('Could not remove', getAiErrorMessage(err)),
              onSettled: () => setBusyId(null),
            });
          },
        },
      ],
    );
  }

  if (!env.hasAiApi) {
    return (
      <SafeAreaView style={styles.flex} edges={['bottom']}>
        <Stack.Screen options={{ title: 'Documents' }} />
        <EmptyState
          emoji="🔌"
          title="Document assistant not configured"
          message="Set EXPO_PUBLIC_AI_API_URL in .env and restart the dev server. See services/ai/README.md."
        />
      </SafeAreaView>
    );
  }

  if (isLoading) {
    return (
      <SafeAreaView style={styles.centered} edges={['bottom']}>
        <Stack.Screen options={{ title: 'Documents' }} />
        <ActivityIndicator color={colors.primary} size="large" />
      </SafeAreaView>
    );
  }

  if (isError) {
    return (
      <SafeAreaView style={styles.flex} edges={['bottom']}>
        <Stack.Screen options={{ title: 'Documents' }} />
        <EmptyState
          emoji="⚠️"
          title="Could not load documents"
          message={getAiErrorMessage(error)}
          actionLabel="Try again"
          onAction={() => void refetch()}
        />
      </SafeAreaView>
    );
  }

  const ready = (documents ?? []).filter((d) => d.status === 'ready' && !d.stale_index);

  return (
    <SafeAreaView style={styles.flex} edges={['bottom']}>
      <Stack.Screen options={{ title: 'Documents', headerBackTitle: 'Product' }} />

      <FlatList<ProductDocument>
        data={documents ?? []}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={() => void refetch()}
            tintColor={colors.textMuted}
          />
        }
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.intro}>
              Attach the manual, warranty card or service terms for{' '}
              {product?.name ?? 'this product'}. Once indexed, you can ask questions and get
              answers that cite the page they came from.
            </Text>
            {(documents ?? []).some((d) => d.stale_index) ? (
              <Banner
                tone="warning"
                title="Some documents need reindexing"
                message="They were indexed with an older embedding model, so they are excluded from search until you retry them."
              />
            ) : null}
          </View>
        }
        renderItem={({ item }) => (
          <DocumentRow
            document={item}
            busy={busyId === item.id}
            onPress={() =>
              router.push({ pathname: '/document/[id]', params: { id: item.id } })
            }
            onRetry={() => handleRetry(item)}
            onDelete={() => handleDelete(item)}
          />
        )}
        ListEmptyComponent={
          <EmptyState
            emoji="📎"
            title="No documents yet"
            message="Add a PDF, a text file or a photo of a warranty card. Scans are read with a vision model."
          />
        }
        ListFooterComponent={
          <View style={styles.footer}>
            <Button
              label={addDocument.isPending ? 'Uploading…' : 'Add a document'}
              onPress={() => void handleAdd()}
              loading={addDocument.isPending}
            />
            <Button
              label="Ask about this product"
              variant="secondary"
              disabled={ready.length === 0}
              onPress={() => router.push({ pathname: '/product/ask/[id]', params: { id } })}
            />
            {ready.length === 0 ? (
              <Text style={styles.footerHint}>
                Questions need at least one indexed document.
              </Text>
            ) : null}
          </View>
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
  header: { gap: spacing.md, marginBottom: spacing.xs },
  intro: { ...typography.body, color: colors.textMuted, lineHeight: 21 },
  footer: { gap: spacing.sm, marginTop: spacing.lg },
  footerHint: { ...typography.caption, color: colors.textFaint, textAlign: 'center' },
});
