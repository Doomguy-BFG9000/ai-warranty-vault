import { Stack } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, spacing, typography } from '@/theme';

/**
 * Temporary landing screen for the Phase 0 scaffold.
 * Replaced by the auth gate + tab navigator in Phase 2.
 */
export default function Landing() {
  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.badge}>
        <Text style={styles.badgeText}>🛡️</Text>
      </View>
      <Text style={styles.title}>AI Warranty Vault</Text>
      <Text style={styles.subtitle}>
        Snap a receipt. We extract the product and warranty, track the expiry,
        and remind you before coverage ends.
      </Text>
      <Text style={styles.note}>Scaffold ready — auth & vault coming next.</Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
    gap: spacing.md,
  },
  badge: {
    width: 88,
    height: 88,
    borderRadius: 24,
    backgroundColor: colors.surfaceAlt,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.sm,
  },
  badgeText: { fontSize: 44 },
  title: { ...typography.title, color: colors.text, textAlign: 'center' },
  subtitle: {
    ...typography.body,
    color: colors.textMuted,
    textAlign: 'center',
    lineHeight: 22,
    maxWidth: 320,
  },
  note: {
    ...typography.caption,
    color: colors.textFaint,
    marginTop: spacing.lg,
  },
});
