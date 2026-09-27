import { Stack, router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Alert, Image, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { Screen } from '@/components/Screen';
import { StatusChip } from '@/components/StatusChip';
import { useDeleteProduct, useProduct } from '@/hooks/useProducts';
import { useReceiptUrl } from '@/hooks/useReceiptUrl';
import { getDatabaseErrorMessage } from '@/lib/errors';
import { formatPrice, getWarrantyState } from '@/lib/warranty';
import { colors, radii, spacing, typography } from '@/theme';

export default function ProductDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: product, isLoading, isError, error } = useProduct(id);
  const { data: receiptUrl } = useReceiptUrl(product?.receipt_image_path);
  const deleteProduct = useDeleteProduct();
  const [deleting, setDeleting] = useState(false);

  function confirmDelete() {
    if (!product) return;

    Alert.alert(
      'Delete this product?',
      'The product and its receipt photo will be permanently removed.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            setDeleting(true);
            try {
              await deleteProduct.mutateAsync(product);
              router.back();
            } catch (err) {
              setDeleting(false);
              Alert.alert('Could not delete', getDatabaseErrorMessage(err));
            }
          },
        },
      ],
    );
  }

  if (isLoading) {
    return (
      <Screen contentStyle={styles.centered}>
        <Stack.Screen options={{ title: 'Loading…' }} />
        <ActivityIndicator color={colors.primary} size="large" />
      </Screen>
    );
  }

  if (isError || !product) {
    return (
      <Screen contentStyle={styles.centered}>
        <Stack.Screen options={{ title: 'Not found' }} />
        <Text style={styles.emoji}>🔍</Text>
        <Text style={styles.title}>This product is not available</Text>
        <Text style={styles.muted}>{getDatabaseErrorMessage(error)}</Text>
        <Button label="Back to vault" variant="secondary" onPress={() => router.back()} />
      </Screen>
    );
  }

  const state = getWarrantyState(product.warranty_expires_at);
  const price = formatPrice(product.price_cents, product.currency);

  return (
    <Screen scroll>
      <Stack.Screen options={{ title: product.name }} />

      <View style={styles.header}>
        <Text style={styles.name}>{product.name}</Text>
        <StatusChip state={state} />
      </View>

      <View style={styles.card}>
        <DetailRow label="Store" value={product.merchant} />
        <DetailRow label="Purchase date" value={product.purchase_date} />
        <DetailRow
          label="Warranty"
          value={product.warranty_months ? `${product.warranty_months} months` : null}
        />
        <DetailRow label="Expires" value={product.warranty_expires_at} />
        <DetailRow label="Price" value={price} />
        <DetailRow label="Category" value={product.category} last />
      </View>

      {product.receipt_image_path ? (
        <View style={styles.receiptSection}>
          <Text style={styles.sectionLabel}>Receipt</Text>
          {receiptUrl ? (
            <Image
              source={{ uri: receiptUrl }}
              style={styles.receipt}
              resizeMode="contain"
              accessibilityLabel={`Receipt for ${product.name}`}
            />
          ) : (
            <View style={[styles.receipt, styles.receiptPlaceholder]}>
              <ActivityIndicator color={colors.textFaint} />
            </View>
          )}
        </View>
      ) : null}

      <View style={styles.actions}>
        <Button
          label="Documents & questions"
          onPress={() =>
            router.push({ pathname: '/product/documents/[id]', params: { id: product.id } })
          }
          disabled={deleting}
        />
        <Button
          label="Edit details"
          variant="secondary"
          onPress={() =>
            router.push({ pathname: '/product/edit/[id]', params: { id: product.id } })
          }
          disabled={deleting}
        />
        <Button label="Delete" variant="danger" onPress={confirmDelete} loading={deleting} />
      </View>
    </Screen>
  );
}

/** One label/value line. Unknown values read as an em dash rather than vanishing. */
function DetailRow({
  label,
  value,
  last = false,
}: {
  label: string;
  value: string | null;
  last?: boolean;
}) {
  return (
    <View style={[styles.row, !last && styles.rowDivider]}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={[styles.rowValue, !value && styles.rowValueEmpty]}>{value || '—'}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.md },
  emoji: { fontSize: 48 },
  title: { ...typography.heading, color: colors.text, textAlign: 'center' },
  muted: { ...typography.body, color: colors.textMuted, textAlign: 'center' },
  header: { gap: spacing.md },
  name: { ...typography.title, color: colors.text },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.lg,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: spacing.lg,
    paddingVertical: spacing.md,
  },
  rowDivider: { borderBottomWidth: 1, borderBottomColor: colors.border },
  rowLabel: { ...typography.body, color: colors.textMuted },
  rowValue: { ...typography.body, color: colors.text, flexShrink: 1, textAlign: 'right' },
  rowValueEmpty: { color: colors.textFaint },
  receiptSection: { gap: spacing.sm },
  sectionLabel: { ...typography.label, color: colors.textMuted },
  receipt: {
    width: '100%',
    height: 320,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
  },
  receiptPlaceholder: { alignItems: 'center', justifyContent: 'center' },
  actions: { gap: spacing.sm, marginTop: spacing.sm },
});
