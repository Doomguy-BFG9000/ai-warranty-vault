import { Stack, router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Banner } from '@/components/Banner';
import { Button } from '@/components/Button';
import { ChatBubble } from '@/components/ChatBubble';
import { EmptyState } from '@/components/EmptyState';
import {
  useCreateThread,
  useResumeThread,
  useSendMessage,
  useThread,
  useThreads,
} from '@/hooks/useAssistant';
import { useProduct } from '@/hooks/useProducts';
import { useProductDocuments } from '@/hooks/useProductDocuments';
import { getAiErrorMessage } from '@/lib/aiApi';
import { withOptimisticMessage } from '@/lib/chat';
import { env } from '@/lib/env';
import { colors, radii, spacing, typography } from '@/theme';
import type { AssistantMessage, Citation } from '@/types/ai';

/**
 * Ask questions about one product's documents.
 *
 * The transcript, and whatever the assistant is waiting for, both come from the
 * backend — nothing about the conversation lives in this component's state. That is
 * what makes closing the app mid-flow and reopening it work: on mount the screen finds
 * the most recent thread for this product and restores it, pending question or pending
 * draft included.
 */
export default function AskProductScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: product } = useProduct(id);
  const { data: documents } = useProductDocuments(id);
  const { data: threads, isLoading: loadingThreads } = useThreads(id);

  const createThread = useCreateThread();
  const [createdThreadId, setCreatedThreadId] = useState<string | null>(null);

  /**
   * Continue the most recent conversation for this product rather than starting a new
   * one on every visit: a pending clarification or an unapproved draft belongs to a
   * thread, and a fresh thread would orphan it.
   *
   * Derived, not synchronised in an effect. A thread is only created when the user
   * actually says something, which also means opening the screen and backing out does
   * not leave an empty conversation behind.
   */
  const threadId = createdThreadId ?? threads?.[0]?.thread_id ?? null;

  const { data: thread, isLoading: loadingThread, refetch } = useThread(threadId ?? undefined);
  const sendMessage = useSendMessage();
  const resume = useResumeThread(threadId ?? undefined);

  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  /**
   * What the user just sent, shown immediately.
   *
   * A turn can take many seconds — retrieval, then a model call — and without this
   * the message vanished from the input and reappeared only when the server answered,
   * which reads as the app having dropped it.
   *
   * It is *hidden* rather than cleared once the server transcript contains it, so
   * there is no gap between the optimistic bubble disappearing and the real one
   * arriving. Derived, not synchronised in an effect.
   */
  const [sent, setSent] = useState<string | null>(null);
  const listRef = useRef<FlatList<AssistantMessage>>(null);

  const messages = thread?.messages ?? [];
  const rendered = withOptimisticMessage(messages, sent);

  useEffect(() => {
    if (rendered.length > 0) {
      // Small delay so the list has laid out the new row before scrolling to it.
      const timer = setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 80);
      return () => clearTimeout(timer);
    }
  }, [rendered.length]);

  const awaiting = thread?.awaiting ?? null;
  const busy = sendMessage.isPending || resume.isPending || createThread.isPending;
  const indexedCount = (documents ?? []).filter((d) => d.status === 'ready' && !d.stale_index).length;

  async function handleSend() {
    const text = draft.trim();
    if (!text || busy) return;
    setError(null);
    setDraft('');
    setSent(text);

    // A pending question is answered through resume, not as a new message: sending a
    // new question while one is outstanding would strand it.
    if (threadId && awaiting?.kind === 'clarification') {
      resume.mutate(
        { answer: text },
        {
          onError: (err) => {
            setSent(null);
            setDraft(text);
            setError(getAiErrorMessage(err));
          },
        },
      );
      return;
    }

    try {
      // The thread is created on the first message, so the id may be brand new here.
      const active = threadId ?? (await createThread.mutateAsync(id)).thread_id;
      setCreatedThreadId(active);
      await sendMessage.mutateAsync({ threadId: active, content: text });
    } catch (err) {
      // Put the text back rather than losing what they typed, and take the optimistic
      // bubble away — it was never delivered.
      setSent(null);
      setDraft(text);
      setError(getAiErrorMessage(err));
    }
  }

  function openCitation(citation: Citation) {
    router.push({
      pathname: '/document/[id]',
      params: {
        id: citation.document_id,
        chunk: citation.chunk_id,
        page: citation.page_number ?? '',
      },
    });
  }

  if (!env.hasAiApi) {
    return (
      <SafeAreaView style={styles.flex} edges={['bottom']}>
        <Stack.Screen options={{ title: 'Ask' }} />
        <EmptyState
          emoji="🔌"
          title="Document assistant not configured"
          message="Set EXPO_PUBLIC_AI_API_URL in .env and restart the dev server."
        />
      </SafeAreaView>
    );
  }

  if (indexedCount === 0) {
    return (
      <SafeAreaView style={styles.flex} edges={['bottom']}>
        <Stack.Screen options={{ title: 'Ask' }} />
        <EmptyState
          emoji="📎"
          title="Nothing to read yet"
          message="Attach a manual, warranty card or service terms first. Answers are only ever built from documents you have added."
          actionLabel="Add a document"
          onAction={() => router.replace({ pathname: '/product/documents/[id]', params: { id } })}
        />
      </SafeAreaView>
    );
  }

  if (loadingThreads || (threadId && loadingThread)) {
    return (
      <SafeAreaView style={styles.centered} edges={['bottom']}>
        <Stack.Screen options={{ title: 'Ask' }} />
        <ActivityIndicator color={colors.primary} size="large" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.flex} edges={['bottom']}>
      <Stack.Screen
        options={{ title: product?.name ?? 'Ask', headerBackTitle: 'Product' }}
      />

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <FlatList<AssistantMessage>
          ref={listRef}
          data={rendered}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.list}
          renderItem={({ item }) => (
            <ChatBubble message={item} onOpenCitation={openCitation} />
          )}
          ListEmptyComponent={
            <View style={styles.suggestions}>
              <Text style={styles.suggestionsTitle}>Try asking</Text>
              {[
                'Jak wyczyścić filtr tego urządzenia?',
                'Co oznacza kod E17?',
                'Czy gwarancja obejmuje uszkodzenie przewodu?',
                'Przygotuj zgłoszenie serwisowe.',
              ].map((suggestion) => (
                <Pressable
                  key={suggestion}
                  accessibilityRole="button"
                  onPress={() => setDraft(suggestion)}
                  style={({ pressed }) => [styles.suggestion, pressed && styles.pressed]}
                >
                  <Text style={styles.suggestionText}>{suggestion}</Text>
                </Pressable>
              ))}
              <Text style={styles.suggestionsHint}>
                Answers come only from the {indexedCount} indexed document
                {indexedCount === 1 ? '' : 's'} on this product, and always cite the page.
              </Text>
            </View>
          }
          ListFooterComponent={
            <View style={styles.footer}>
              {busy ? (
                <View style={styles.thinking}>
                  <ActivityIndicator size="small" color={colors.textMuted} />
                  <Text style={styles.thinkingText}>Reading the documents…</Text>
                </View>
              ) : null}

              {awaiting?.kind === 'claim_draft' && awaiting.draft ? (
                <View style={styles.draftPrompt}>
                  <Banner
                    tone="info"
                    title="A draft is waiting for you"
                    message="Nothing is sent. Read it, edit anything you want changed, then approve or discard."
                  />
                  <Button
                    label="Review the draft"
                    onPress={() =>
                      router.push({
                        pathname: '/claim/[threadId]',
                        params: { threadId: threadId ?? '' },
                      })
                    }
                  />
                </View>
              ) : null}

              {error ? <Banner tone="danger" title="That did not work" message={error} /> : null}
            </View>
          }
          onScrollBeginDrag={() => setError(null)}
        />

        <View style={styles.composer}>
          {/*
            No reply-context chip above the input. Two attempts at one — a line of
            prose, then a labelled chip — both restated the clarifying question that
            is already the last bubble in the transcript, one scroll-line above. The
            placeholder ("Your answer…") carries the same information without
            repeating the question.
          */}
          <View style={styles.composerRow}>
            <TextInput
              accessibilityLabel="Your question"
              value={draft}
              onChangeText={setDraft}
              placeholder={
                awaiting?.kind === 'clarification' ? 'Your answer…' : 'Ask about this product…'
              }
              placeholderTextColor={colors.textFaint}
              style={styles.input}
              multiline
              editable={!busy && awaiting?.kind !== 'claim_draft'}
              onSubmitEditing={() => void handleSend()}
            />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Send"
              disabled={busy || !draft.trim() || awaiting?.kind === 'claim_draft'}
              onPress={() => void handleSend()}
              style={({ pressed }) => [
                styles.send,
                (busy || !draft.trim() || awaiting?.kind === 'claim_draft') && styles.sendDisabled,
                pressed && styles.pressed,
              ]}
            >
              <Text style={styles.sendLabel}>↑</Text>
            </Pressable>
          </View>
          {awaiting?.kind === 'claim_draft' ? (
            <Text style={styles.awaitingHint}>
              Decide on the pending draft before asking anything else.
            </Text>
          ) : null}
          <Pressable accessibilityRole="button" onPress={() => void refetch()}>
            <Text style={styles.refresh}>Refresh from server</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
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
  pressed: { opacity: 0.7 },
  suggestions: { gap: spacing.sm },
  suggestionsTitle: { ...typography.label, color: colors.textMuted },
  suggestion: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
  },
  suggestionText: { ...typography.body, color: colors.text },
  suggestionsHint: {
    ...typography.caption,
    color: colors.textFaint,
    marginTop: spacing.sm,
    lineHeight: 17,
  },
  footer: { gap: spacing.md, marginTop: spacing.sm },
  thinking: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  thinkingText: { ...typography.caption, color: colors.textMuted },
  draftPrompt: { gap: spacing.sm },
  composer: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surface,
    padding: spacing.md,
    gap: spacing.xs,
  },
  composerRow: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.sm },
  input: {
    ...typography.body,
    flex: 1,
    color: colors.text,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    maxHeight: 120,
  },
  send: {
    width: 44,
    height: 44,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendDisabled: { opacity: 0.4 },
  sendLabel: { ...typography.heading, color: colors.onPrimary },
  awaitingHint: { ...typography.caption, color: colors.primary },
  refresh: { ...typography.caption, color: colors.textFaint, textAlign: 'center' },
});
