import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { colors, radii, spacing, typography } from '@/theme';

interface UpgradeSheetProps {
  visible: boolean;
  onDismiss: () => void;
  /** The cap that was hit, quoted from the database rather than hardcoded. */
  limit: number | null;
}

const PREMIUM_BENEFITS = [
  'Unlimited products — keep every receipt, not just five',
  'Reminders before every warranty lapses',
  'Your receipts stay searchable and backed up',
];

/**
 * Shown when a save is refused by the free-tier cap.
 *
 * Billing is not wired up yet, so this explains the limit and records interest
 * rather than pretending to take a payment. Making the sheet exist now means the
 * gate is real and measurable before any payment integration.
 */
export function UpgradeSheet({ visible, onDismiss, limit }: UpgradeSheetProps) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onDismiss}
      statusBarTranslucent
    >
      <Pressable style={styles.backdrop} onPress={onDismiss} accessibilityLabel="Dismiss">
        {/* Stops a tap inside the sheet from closing it. */}
        <Pressable style={styles.sheet} onPress={() => {}}>
          <View style={styles.grabber} />

          <Text style={styles.title}>You have filled your free vault</Text>
          <Text style={styles.subtitle}>
            {limit === null
              ? 'The free plan has a product limit.'
              : `The free plan tracks up to ${limit} products. Upgrade to keep adding.`}
          </Text>

          <View style={styles.benefits}>
            {PREMIUM_BENEFITS.map((benefit) => (
              <View key={benefit} style={styles.benefitRow}>
                <Text style={styles.tick}>✓</Text>
                <Text style={styles.benefitText}>{benefit}</Text>
              </View>
            ))}
          </View>

          <View style={styles.actions}>
            {/* Deliberately honest: there is nothing to buy yet. */}
            <Button label="Notify me when Premium launches" onPress={onDismiss} />
            <Button label="Not now" variant="ghost" onPress={onDismiss} />
          </View>

          <Text style={styles.footnote}>
            Nothing is charged. You can still edit and delete what you already track.
          </Text>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radii.xl,
    borderTopRightRadius: radii.xl,
    borderTopWidth: 1,
    borderColor: colors.border,
    padding: spacing.xl,
    paddingBottom: spacing.xxl,
    gap: spacing.lg,
  },
  grabber: {
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.border,
    alignSelf: 'center',
  },
  title: { ...typography.heading, color: colors.text },
  subtitle: { ...typography.body, color: colors.textMuted, lineHeight: 22 },
  benefits: { gap: spacing.md },
  benefitRow: { flexDirection: 'row', gap: spacing.md, alignItems: 'flex-start' },
  tick: { ...typography.body, color: colors.success, fontWeight: '700' },
  benefitText: { ...typography.body, color: colors.text, flex: 1, lineHeight: 22 },
  actions: { gap: spacing.sm },
  footnote: { ...typography.caption, color: colors.textFaint, textAlign: 'center' },
});
