import { Stack, router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Alert, StyleSheet, Text, View } from 'react-native';

import { Banner } from '@/components/Banner';
import { Button } from '@/components/Button';
import { CitationList } from '@/components/CitationList';
import { EmptyState } from '@/components/EmptyState';
import { Screen } from '@/components/Screen';
import { TextField } from '@/components/TextField';
import { useResumeThread, useThread } from '@/hooks/useAssistant';
import { getAiErrorMessage } from '@/lib/aiApi';
import { colors, radii, spacing, typography } from '@/theme';

/**
 * Review, edit and decide on a drafted service request.
 *
 * Nothing here sends anything. Approving stores the exact text shown in the fields, so
 * what gets recorded is what the person read and signed off — not a later regeneration
 * of it. Approving twice is safe: the server reports the second attempt as "already
 * decided" and does not rewrite the stored version.
 */
export default function ClaimDraftScreen() {
  const { threadId } = useLocalSearchParams<{ threadId: string }>();
  const { data: thread, isLoading, isError, error } = useThread(threadId);
  const resume = useResumeThread(threadId);

  const pending = thread?.awaiting?.kind === 'claim_draft' ? thread.awaiting.draft : null;
  const latest = thread?.latest_draft ?? null;
  const draft = pending ?? latest;

  /**
   * Local edits start as null and the server's text is the fallback, rather than being
   * copied into state when it arrives. That way a refetch cannot stamp on an edit in
   * progress, and there is no window where the fields are empty because the draft has
   * loaded but the copy has not happened yet.
   */
  const [subjectEdit, setSubjectEdit] = useState<string | null>(null);
  const [bodyEdit, setBodyEdit] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const subject = subjectEdit ?? draft?.approved_subject ?? draft?.subject ?? '';
  const body = bodyEdit ?? draft?.approved_body ?? draft?.body ?? '';

  function decide(decision: 'approve' | 'reject') {
    if (!pending) return;
    setProblem(null);
    resume.mutate(
      decision === 'approve' ? { decision, subject, body } : { decision },
      {
        onSuccess: (result) => {
          if (result.draft_result && result.draft_result.applied === false) {
            // Not an error: a retried request, or a second tap. The stored approval is
            // untouched, and saying so is more useful than a silent success.
            Alert.alert(
              'Already decided',
              'This draft had already been decided, so nothing was changed.',
            );
          }
          router.back();
        },
        onError: (err) => setProblem(getAiErrorMessage(err)),
      },
    );
  }

  if (isLoading) {
    return (
      <Screen contentStyle={styles.centered}>
        <Stack.Screen options={{ title: 'Service request' }} />
        <ActivityIndicator color={colors.primary} size="large" />
      </Screen>
    );
  }

  if (isError || !thread) {
    return (
      <Screen contentStyle={styles.centered}>
        <Stack.Screen options={{ title: 'Service request' }} />
        <EmptyState emoji="🔍" title="This conversation is not available" message={getAiErrorMessage(error)} />
      </Screen>
    );
  }

  if (!draft) {
    return (
      <Screen contentStyle={styles.centered}>
        <Stack.Screen options={{ title: 'Service request' }} />
        <EmptyState
          emoji="✉️"
          title="No draft yet"
          message='Ask the assistant to prepare a service request, for example "Przygotuj zgłoszenie serwisowe".'
        />
      </Screen>
    );
  }

  const decided = !pending;

  return (
    <Screen scroll keyboardAvoiding>
      <Stack.Screen options={{ title: 'Service request', headerBackTitle: 'Back' }} />

      <Banner
        tone={decided ? 'success' : 'info'}
        title={
          decided
            ? draft.status === 'approved'
              ? 'Approved and saved'
              : draft.status === 'rejected'
                ? 'Discarded'
                : 'Superseded by a newer draft'
            : 'Nothing is sent'
        }
        message={
          decided
            ? draft.status === 'approved'
              ? 'This is the version you approved. The app does not contact the seller — copy it wherever you need it.'
              : null
            : 'This is a draft for you to check. Approving it only stores the text; the app never contacts the seller.'
        }
      />

      {draft.missing_information.length > 0 && !decided ? (
        <View style={styles.missing}>
          <Text style={styles.missingTitle}>You may want to add</Text>
          {draft.missing_information.map((item) => (
            <Text key={item} style={styles.missingItem}>
              • {item}
            </Text>
          ))}
          <Text style={styles.missingHint}>
            These were left out deliberately rather than guessed at.
          </Text>
        </View>
      ) : null}

      <TextField
        label="Subject"
        value={subject}
        onChangeText={setSubjectEdit}
        editable={!decided && !resume.isPending}
      />
      <TextField
        label="Message"
        value={body}
        onChangeText={setBodyEdit}
        multiline
        editable={!decided && !resume.isPending}
        style={styles.bodyInput}
        hint={decided ? undefined : 'Edit freely — what you approve is what gets stored.'}
      />

      <CitationList
        citations={draft.citations}
        onOpen={(citation) =>
          router.push({
            pathname: '/document/[id]',
            params: { id: citation.document_id, chunk: citation.chunk_id },
          })
        }
      />

      {problem ? <Banner tone="danger" title="That did not work" message={problem} /> : null}

      {decided ? (
        <Text style={styles.decidedNote}>
          {draft.approved_at
            ? `Approved ${new Date(draft.approved_at).toLocaleString()} · version ${draft.version}`
            : `Version ${draft.version}`}
        </Text>
      ) : (
        <View style={styles.actions}>
          <Button
            label="Approve and save"
            onPress={() => decide('approve')}
            loading={resume.isPending}
            disabled={!subject.trim() || !body.trim()}
          />
          <Button
            label="Discard this draft"
            variant="secondary"
            onPress={() => decide('reject')}
            disabled={resume.isPending}
          />
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  bodyInput: { minHeight: 220, textAlignVertical: 'top' },
  missing: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    padding: spacing.lg,
    gap: spacing.xs,
  },
  missingTitle: { ...typography.label, color: colors.textMuted },
  missingItem: { ...typography.body, color: colors.text },
  missingHint: { ...typography.caption, color: colors.textFaint, marginTop: spacing.xs },
  actions: { gap: spacing.sm, marginTop: spacing.sm },
  decidedNote: { ...typography.caption, color: colors.textFaint, textAlign: 'center' },
});
