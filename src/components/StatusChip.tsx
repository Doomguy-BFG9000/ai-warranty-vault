import { StyleSheet, Text, View } from 'react-native';

import { radii, spacing, typography } from '@/theme';
import type { WarrantyState } from '@/lib/warranty';

/**
 * Warranty status pill. Colour carries the urgency, but the label repeats it in
 * words so the meaning does not depend on colour perception alone.
 */
export function StatusChip({ state }: { state: WarrantyState }) {
  return (
    <View style={[styles.chip, { backgroundColor: `${state.color}22`, borderColor: state.color }]}>
      <View style={[styles.dot, { backgroundColor: state.color }]} />
      <Text style={[styles.label, { color: state.color }]}>{state.label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
    borderWidth: 1,
    alignSelf: 'flex-start',
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  label: { ...typography.caption, fontWeight: '600' },
});
