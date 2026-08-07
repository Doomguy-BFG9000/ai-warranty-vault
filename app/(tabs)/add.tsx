import { StyleSheet, Text } from 'react-native';

import { Screen } from '@/components/Screen';
import { colors, spacing, typography } from '@/theme';

/** Placeholder until receipt capture lands in Phase 3. */
export default function AddScreen() {
  return (
    <Screen edges={['bottom']} contentStyle={styles.centered}>
      <Text style={styles.emoji}>📸</Text>
      <Text style={styles.title}>Add a receipt</Text>
      <Text style={styles.subtitle}>Camera capture and AI extraction land next.</Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emoji: { fontSize: 56, marginBottom: spacing.sm },
  title: { ...typography.heading, color: colors.text },
  subtitle: { ...typography.body, color: colors.textMuted, textAlign: 'center' },
});
