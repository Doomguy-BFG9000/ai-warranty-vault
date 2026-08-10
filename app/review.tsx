import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { Screen } from '@/components/Screen';
import { TextField } from '@/components/TextField';
import { useAuth } from '@/hooks/useAuth';
import { useExtractReceipt } from '@/hooks/useExtractReceipt';
import { useCreateProduct } from '@/hooks/useProducts';
import { getDatabaseErrorMessage, isFreeTierLimitError } from '@/lib/errors';
import { deleteReceiptImage } from '@/lib/receipts';
import {
  centsToPriceInput,
  normaliseCurrency,
  priceToCents,
  validatePrice,
  validatePurchaseDate,
  validateWarrantyMonths,
} from '@/lib/validation';
import { colors, radii, spacing, typography } from '@/theme';
import type { ReceiptExtraction } from '@/types/extraction';

type FieldErrors = Partial<Record<'name' | 'purchaseDate' | 'warrantyMonths' | 'price', string>>;

/**
 * Confirmation step for a captured receipt.
 *
 * Claude's extraction pre-fills these fields, but nothing is written until the
 * user confirms: the model is an assistant here, not the authority. Extraction
 * failing is not a dead end — the same form works when typed in by hand.
 */
export default function ReviewScreen() {
  const { receiptPath } = useLocalSearchParams<{ receiptPath?: string }>();
  const { user } = useAuth();
  const createProduct = useCreateProduct();
  const extract = useExtractReceipt();

  const [name, setName] = useState('');
  const [merchant, setMerchant] = useState('');
  const [purchaseDate, setPurchaseDate] = useState('');
  const [warrantyMonths, setWarrantyMonths] = useState('');
  const [price, setPrice] = useState('');
  const [errors, setErrors] = useState<FieldErrors>({});

  // Fields Claude filled that have no input of their own, carried through to the insert.
  const [category, setCategory] = useState<string | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);
  const [extraction, setExtraction] = useState<ReceiptExtraction | null>(null);

  // Guards against the effect firing twice (React 18 strict mode double-invoke),
  // which would bill a second extraction for the same receipt.
  const startedRef = useRef(false);

  useEffect(() => {
    if (!receiptPath || startedRef.current) return;
    startedRef.current = true;

    extract
      .mutateAsync(receiptPath)
      .then((result) => {
        setExtraction(result);
        applyExtraction(result);
      })
      .catch(() => {
        // Swallowed on purpose: the form below is still usable, and
        // extract.isError drives the explanatory banner.
      });
  }, [receiptPath, extract]);

  function applyExtraction(result: ReceiptExtraction) {
    if (result.product_name) setName(result.product_name);
    if (result.merchant) setMerchant(result.merchant);
    if (result.purchase_date) setPurchaseDate(result.purchase_date);
    if (result.warranty_months !== null) setWarrantyMonths(String(result.warranty_months));
    if (result.price_cents !== null) setPrice(centsToPriceInput(result.price_cents));
    setCategory(result.category);
    setCurrency(normaliseCurrency(result.currency));
  }

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
        category,
        // The column is NOT NULL with a USD default, so only override it when
        // the receipt actually told us something.
        ...(currency ? { currency } : {}),
        receipt_image_path: receiptPath ?? null,
        extraction_status: extraction ? 'done' : 'failed',
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

  if (extract.isPending) {
    return (
      <Screen edges={['bottom']} contentStyle={styles.centered}>
        <ActivityIndicator color={colors.primary} size="large" />
        <Text style={styles.loadingTitle}>Reading your receipt…</Text>
        <Text style={styles.loadingHint}>
          Pulling out the product, date and warranty. You will get to check it all.
        </Text>
      </Screen>
    );
  }

  return (
    <Screen scroll keyboardAvoiding edges={['bottom']}>
      <ReviewBanner extraction={extraction} failed={extract.isError} />

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
        label={currency ? `Price (${currency})` : 'Price'}
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

/** Explains where the pre-filled values came from, and how much to trust them. */
function ReviewBanner({
  extraction,
  failed,
}: {
  extraction: ReceiptExtraction | null;
  failed: boolean;
}) {
  if (failed) {
    return (
      <View style={[styles.banner, styles.bannerWarn]}>
        <Text style={styles.bannerText}>
          We could not read this receipt automatically. Fill in what you can — the
          photo is still saved with the product.
        </Text>
      </View>
    );
  }

  if (extraction?.not_a_receipt) {
    return (
      <View style={[styles.banner, styles.bannerWarn]}>
        <Text style={styles.bannerText}>
          This does not look like a receipt. You can still save it, or cancel and
          retake the photo.
        </Text>
      </View>
    );
  }

  if (extraction?.confidence === 'low') {
    return (
      <View style={[styles.banner, styles.bannerWarn]}>
        <Text style={styles.bannerText}>
          The photo was hard to read, so please double-check these details before
          saving.
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.banner}>
      <Text style={styles.bannerText}>
        {extraction
          ? 'Filled in from your receipt — check the details and change anything that is off.'
          : 'Check these details before saving — you can change anything.'}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.md },
  loadingTitle: { ...typography.heading, color: colors.text, marginTop: spacing.sm },
  loadingHint: {
    ...typography.body,
    color: colors.textMuted,
    textAlign: 'center',
    maxWidth: 300,
    lineHeight: 22,
  },
  banner: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  bannerWarn: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.warning,
  },
  bannerText: { ...typography.caption, color: colors.textMuted, lineHeight: 18 },
  actions: { gap: spacing.sm, marginTop: spacing.md },
});
