import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { Screen } from '@/components/Screen';
import { useAuth } from '@/hooks/useAuth';
import { useProfile } from '@/hooks/useProfile';
import { colors, radii, spacing, typography } from '@/theme';

export default function SettingsScreen() {
  const { user, signOut } = useAuth();
  const { data: profile, isLoading } = useProfile();
  const queryClient = useQueryClient();
  const [signingOut, setSigningOut] = useState(false);

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await signOut();
      // Drop cached rows so the next account cannot briefly see them.
      queryClient.clear();
    } catch {
      Alert.alert('Sign out failed', 'Please try again.');
      setSigningOut(false);
    }
  }

  function confirmSignOut() {
    Alert.alert('Sign out?', 'You will need to sign in again to reach your vault.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign out', style: 'destructive', onPress: handleSignOut },
    ]);
  }

  const isPremium = profile?.is_premium ?? false;

  return (
    <Screen edges={['bottom']} scroll>
      <View style={styles.card}>
        <Text style={styles.cardLabel}>Signed in as</Text>
        <Text style={styles.email}>{user?.email ?? '—'}</Text>
      </View>

      <View style={styles.card}>
        <View style={styles.planRow}>
          <View style={styles.planText}>
            <Text style={styles.cardLabel}>Plan</Text>
            <Text style={styles.planName}>
              {isLoading ? 'Loading…' : isPremium ? 'Premium' : 'Free'}
            </Text>
          </View>
          <View style={[styles.badge, isPremium ? styles.badgePremium : styles.badgeFree]}>
            <Text style={styles.badgeText}>{isPremium ? '★ Premium' : 'Free'}</Text>
          </View>
        </View>
        <Text style={styles.planHint}>
          {isPremium
            ? 'Unlimited products and reminders.'
            : 'Track up to 5 products. Upgrade for unlimited storage.'}
        </Text>
      </View>

      <Button
        label="Sign out"
        variant="secondary"
        onPress={confirmSignOut}
        loading={signingOut}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.xs,
  },
  cardLabel: { ...typography.label, color: colors.textMuted },
  email: { ...typography.subheading, color: colors.text },
  planRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  planText: { gap: spacing.xs },
  planName: { ...typography.subheading, color: colors.text },
  planHint: { ...typography.caption, color: colors.textFaint, marginTop: spacing.xs },
  badge: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
  },
  badgeFree: { backgroundColor: colors.surfaceAlt },
  badgePremium: { backgroundColor: colors.primary },
  badgeText: { ...typography.caption, color: colors.text, fontWeight: '600' },
});
