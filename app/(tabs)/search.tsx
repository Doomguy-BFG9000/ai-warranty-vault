import { router } from 'expo-router';
import { useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Banner } from '@/components/Banner';
import { EmptyState } from '@/components/EmptyState';
import { useDocumentSearch } from '@/hooks/useDocumentSearch';
import { getAiErrorMessage } from '@/lib/aiApi';
import { env } from '@/lib/env';
import { colors, radii, spacing, typography } from '@/theme';
import type { SearchHit, SearchMode } from '@/types/ai';

const MODES: { mode: SearchMode; label: string; hint: string }[] = [
  { mode: 'hybrid', label: 'Hybrid', hint: 'Meaning and exact wording, combined' },
  { mode: 'vector', label: 'Meaning', hint: 'Semantic similarity only' },
  { mode: 'lexical', label: 'Exact', hint: 'Literal words, codes and part numbers' },
];

/**
 * Search across every document the signed-in user owns.
 *
 * The mode switch is deliberately visible. It is the quickest way to see why hybrid
 * retrieval exists: type an error code and compare "Exact" with "Meaning", then try a
 * paraphrase and compare them the other way round.
 */
export default function SearchScreen() {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<SearchMode>('hybrid');
  const search = useDocumentSearch();

  function run(nextMode: SearchMode = mode) {
    const text = query.trim();
    if (!text) return;
    setMode(nextMode);
    search.mutate({ query: text, mode: nextMode, topK: 10 });
  }

  if (!env.hasAiApi) {
    return (
      <SafeAreaView style={styles.flex} edges={['bottom']}>
        <EmptyState
          emoji="🔌"
          title="Document search not configured"
          message="Set EXPO_PUBLIC_AI_API_URL in .env and restart the dev server. See services/ai/README.md."
        />
      </SafeAreaView>
    );
  }

  const result = search.data;

  return (
    <SafeAreaView style={styles.flex} edges={['bottom']}>
      <View style={styles.searchBar}>
        <TextInput
          accessibilityLabel="Search your documents"
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={() => run()}
          returnKeyType="search"
          placeholder="Error code, model number, or a question…"
          placeholderTextColor={colors.textFaint}
          style={styles.input}
        />
        <View style={styles.modes}>
          {MODES.map((option) => (
            <Pressable
              key={option.mode}
              accessibilityRole="button"
              accessibilityState={{ selected: mode === option.mode }}
              onPress={() => run(option.mode)}
              style={({ pressed }) => [
                styles.mode,
                mode === option.mode && styles.modeActive,
                pressed && styles.pressed,
              ]}
            >
              <Text style={[styles.modeLabel, mode === option.mode && styles.modeLabelActive]}>
                {option.label}
              </Text>
            </Pressable>
          ))}
        </View>
        <Text style={styles.modeHint}>{MODES.find((m) => m.mode === mode)?.hint}</Text>
      </View>

      {search.isPending ? (
        <View style={styles.centered}>
          <ActivityIndicator color={colors.primary} size="large" />
        </View>
      ) : search.isError ? (
        <EmptyState
          emoji="⚠️"
          title="Search failed"
          message={getAiErrorMessage(search.error)}
          actionLabel="Try again"
          onAction={() => run()}
        />
      ) : !result ? (
        <EmptyState
          emoji="🔎"
          title="Search your documents"
          message="Finds the passage even when you word it differently, and matches error codes and part numbers exactly."
        />
      ) : result.no_documents ? (
        <EmptyState
          emoji="📎"
          title="Nothing indexed yet"
          message="Open a product and attach its manual or warranty card. Search only covers documents you have added."
        />
      ) : result.hits.length === 0 ? (
        <EmptyState
          emoji="🤔"
          title="No passage matched"
          message="Try different wording, or check that the document covering this is attached and indexed."
        />
      ) : (
        <FlatList<SearchHit>
          data={result.hits}
          keyExtractor={(item) => item.chunk_id}
          contentContainerStyle={styles.list}
          ListHeaderComponent={
            <Banner
              tone="info"
              title={`${result.hits.length} passage${result.hits.length === 1 ? '' : 's'} · ${result.mode}`}
              message="Tap a result to read the full passage in its document."
            />
          }
          renderItem={({ item }) => (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`${item.document_title ?? 'Document'}, ${item.locator}`}
              onPress={() =>
                router.push({
                  pathname: '/document/[id]',
                  params: { id: item.document_id, chunk: item.chunk_id },
                })
              }
              style={({ pressed }) => [styles.hit, pressed && styles.pressed]}
            >
              <View style={styles.hitHeader}>
                <Text style={styles.hitTitle} numberOfLines={1}>
                  {item.document_title ?? 'Document'}
                </Text>
                <Text style={styles.hitLocator}>{item.locator}</Text>
              </View>
              <Text style={styles.hitSnippet} numberOfLines={4}>
                {item.snippet}
              </Text>
              {/* Shown because it is genuinely useful when tuning retrieval — and
                  labelled as a rank rather than a confidence, which it is not. */}
              <Text style={styles.hitScore}>
                {[
                  item.vector_position ? `meaning #${item.vector_position}` : null,
                  item.lexical_position ? `exact #${item.lexical_position}` : null,
                ]
                  .filter(Boolean)
                  .join(' · ') || 'ranked'}
              </Text>
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  searchBar: {
    padding: spacing.lg,
    gap: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surface,
  },
  input: {
    ...typography.body,
    color: colors.text,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    minHeight: 48,
  },
  modes: { flexDirection: 'row', gap: spacing.sm },
  mode: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceAlt,
  },
  modeActive: { borderColor: colors.primary, backgroundColor: `${colors.primary}22` },
  modeLabel: { ...typography.caption, color: colors.textMuted },
  modeLabelActive: { color: colors.primary, fontWeight: '600' },
  modeHint: { ...typography.caption, color: colors.textFaint },
  pressed: { opacity: 0.7 },
  list: { padding: spacing.lg, gap: spacing.md },
  hit: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  hitHeader: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  hitTitle: { ...typography.subheading, color: colors.text, flex: 1 },
  hitLocator: { ...typography.caption, color: colors.primary },
  hitSnippet: { ...typography.body, color: colors.textMuted, lineHeight: 20 },
  hitScore: { ...typography.caption, color: colors.textFaint },
});
