import { StyleSheet, Text } from 'react-native';

import { Screen } from '@/components/Screen';
import { colors, spacing, typography } from '@/theme';

/** Placeholder until the product list lands in Phase 5. */
export default function VaultScreen() {
  return (
    <Screen edges={['bottom']} contentStyle={styles.centered}>
      <Text style={styles.emoji}>🛡️</Text>
      <Text style={styles.title}>Your vault is empty</Text>
      <Text style={styles.subtitle}>
        Add your first receipt from the Add tab to start tracking warranties.
      </Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emoji: { fontSize: 56, marginBottom: spacing.sm },
  title: { ...typography.heading, color: colors.text },
  subtitle: {
    ...typography.body,
    color: colors.textMuted,
    textAlign: 'center',
    maxWidth: 280,
    lineHeight: 22,
  },
});
