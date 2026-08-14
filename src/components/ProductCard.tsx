import { Pressable, StyleSheet, Text, View } from 'react-native';

import { StatusChip } from '@/components/StatusChip';
import { formatPrice, getWarrantyState } from '@/lib/warranty';
import { colors, radii, spacing, typography } from '@/theme';
import type { Product } from '@/types/models';

interface ProductCardProps {
  product: Product;
  onPress: () => void;
}

/** One row in the vault: what it is, where it came from, and how long it is covered. */
export function ProductCard({ product, onPress }: ProductCardProps) {
  const state = getWarrantyState(product.warranty_expires_at);
  const price = formatPrice(product.price_cents, product.currency);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${product.name}, ${state.label}`}
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
    >
      <View style={styles.header}>
        <Text style={styles.name} numberOfLines={2}>
          {product.name}
        </Text>
        <StatusChip state={state} />
      </View>

      {(product.merchant || price) && (
        <Text style={styles.meta} numberOfLines={1}>
          {[product.merchant, price].filter(Boolean).join(' · ')}
        </Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  pressed: { opacity: 0.7 },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: spacing.md,
  },
  name: { ...typography.subheading, color: colors.text, flex: 1 },
  meta: { ...typography.caption, color: colors.textMuted },
});
