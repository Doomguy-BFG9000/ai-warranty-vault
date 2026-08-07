import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { Screen } from '@/components/Screen';
import { TextField } from '@/components/TextField';
import { useAuth } from '@/hooks/useAuth';
import { useCreateProduct } from '@/hooks/useProducts';
import { getDatabaseErrorMessage, isFreeTierLimitError } from '@/lib/errors';
import { deleteReceiptImage } from '@/lib/receipts';
import {
  priceToCents,
  validatePrice,
  validatePurchaseDate,
  validateWarrantyMonths,
} from '@/lib/validation';
import { colors, radii, spacing, typography } from '@/theme';

type FieldErrors = Partial<Record<'name' | 'purchaseDate' | 'warrantyMonths' | 'price', string>>;

/**
 * Confirmation step for a captured receipt.
 *
 * Everything here is editable on purpose: from Phase 4 these fields arrive
 * pre-filled by Claude, and the user stays the authority on what gets saved.
 */
export default function ReviewScreen() {
  const { receiptPath } = useLocalSearchParams<{ receiptPath?: string }>();
  const { user } = useAuth();
  const createProduct = useCreateProduct();

  const [name, setName] = useState('');
  const [merchant, setMerchant] = useState('');
  const [purchaseDate, setPurchaseDate] = useState('');
  const [warrantyMonths, setWarrantyMonths] = useState('');
  const [price, setPrice] = useState('');
  const [errors, setErrors] = useState<FieldErrors>({});

  function clearError(field: keyof FieldErrors) {
    if (errors[field]) setErrors((prev) => ({ ...prev, [field]: undefined }));
  }

  async function handleSave() {
    const nextErrors: FieldErrors = {
      name: name.trim() ? undefined : 'Product name is required.',
      purchaseDate: validatePurchaseDate(purchaseDate) ?? undefined,
      warrantyMonths: validateWarrantyMonths(warrantyMonths) ?? undefined,
      price: validatePrice(price) ?? undefined,
    };

    if (Object.values(nextErrors).some(Boolean)) {
      setErrors(nextErrors);
      return;
    }

    if (!user) return;

    try {
      await createProduct.mutateAsync({
        user_id: user.id,
        name: name.trim(),
        merchant: merchant.trim() || null,
        purchase_date: purchaseDate.trim() || null,
        warranty_months: warrantyMonths.trim() ? Number(warrantyMonths.trim()) : null,
        price_cents: priceToCents(price),
        receipt_image_path: receiptPath ?? null,
        extraction_status: 'done',
      });

      router.dismissTo('/');
    } catch (error) {
      if (isFreeTierLimitError(error)) {
        Alert.alert(
          'Free plan limit reached',
          'You are tracking the maximum number of products on the free plan. Upgrade for unlimited storage.',
          [{ text: 'OK' }],
        );
        return;
      }
      Alert.alert('Could not save', getDatabaseErrorMessage(error));
    }
  }

  /** Discards the uploaded image so an abandoned capture leaves nothing behind. */
  function handleCancel() {
    if (receiptPath) void deleteReceiptImage(receiptPath).catch(() => {});
    router.back();
  }

  return (
    <Screen scroll keyboardAvoiding edges={['bottom']}>
      <View style={styles.banner}>
        <Text style={styles.bannerText}>
          Check these details before saving — you can change anything.
        </Text>
      </View>

      <TextField
        label="Product"
        value={name}
        onChangeText={(value) => {
          setName(value);
          clearError('name');
        }}
        error={errors.name}
        placeholder="e.g. MacBook Pro 14”"
        autoCapitalize="words"
      />

      <TextField
        label="Store"
        value={merchant}
        onChangeText={setMerchant}
        placeholder="e.g. Apple Store"
        autoCapitalize="words"
      />

      <TextField
        label="Purchase date"
        value={purchaseDate}
        onChangeText={(value) => {
          setPurchaseDate(value);
          clearError('purchaseDate');
        }}
        error={errors.purchaseDate}
        hint="Format: YYYY-MM-DD"
        placeholder="2026-08-07"
        keyboardType="numbers-and-punctuation"
        autoCapitalize="none"
      />

      <TextField
        label="Warranty length (months)"
        value={warrantyMonths}
        onChangeText={(value) => {
          setWarrantyMonths(value);
          clearError('warrantyMonths');
        }}
        error={errors.warrantyMonths}
        hint="Expiry is calculated from the purchase date."
        placeholder="24"
        keyboardType="number-pad"
      />

      <TextField
        label="Price"
        value={price}
        onChangeText={(value) => {
          setPrice(value);
          clearError('price');
        }}
        error={errors.price}
        placeholder="1299.00"
        keyboardType="decimal-pad"
      />

      <View style={styles.actions}>
        <Button
          label="Save to vault"
          onPress={handleSave}
          loading={createProduct.isPending}
        />
        <Button
          label="Cancel"
          variant="ghost"
          onPress={handleCancel}
          disabled={createProduct.isPending}
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  banner: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  bannerText: { ...typography.caption, color: colors.textMuted },
  actions: { gap: spacing.sm, marginTop: spacing.md },
});
