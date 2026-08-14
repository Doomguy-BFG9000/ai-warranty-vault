import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { ProductForm } from '@/components/ProductForm';
import { Screen } from '@/components/Screen';
import { UpgradeSheet } from '@/components/UpgradeSheet';
import { useAuth } from '@/hooks/useAuth';
import { useExtractReceipt } from '@/hooks/useExtractReceipt';
import { useCreateProduct } from '@/hooks/useProducts';
import { useProductQuota } from '@/hooks/useProductQuota';
import { getDatabaseErrorMessage, isFreeTierLimitError } from '@/lib/errors';
import {
  EMPTY_PRODUCT_FORM,
  hasErrors,
  toProductFields,
  validateProductForm,
  type ProductFormErrors,
  type ProductFormField,
  type ProductFormValues,
} from '@/lib/productForm';
import { deleteReceiptImage } from '@/lib/receipts';
import { centsToPriceInput, normaliseCurrency } from '@/lib/validation';
import { colors, radii, spacing, typography } from '@/theme';
import type { ReceiptExtraction } from '@/types/extraction';

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
  const { data: quota } = useProductQuota();

  const [showUpgrade, setShowUpgrade] = useState(false);

  const [values, setValues] = useState<ProductFormValues>(EMPTY_PRODUCT_FORM);
  const [errors, setErrors] = useState<ProductFormErrors>({});

  // Extracted fields with no input of their own, carried through to the insert.
  const [category, setCategory] = useState<string | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);
  const [extraction, setExtraction] = useState<ReceiptExtraction | null>(null);

  // Guards against the effect firing twice (React strict-mode double-invoke),
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
    setValues({
      name: result.product_name ?? '',
      merchant: result.merchant ?? '',
      purchaseDate: result.purchase_date ?? '',
      warrantyMonths: result.warranty_months === null ? '' : String(result.warranty_months),
      price: result.price_cents === null ? '' : centsToPriceInput(result.price_cents),
    });
    setCategory(result.category);
    setCurrency(normaliseCurrency(result.currency));
  }

  function handleChange(field: ProductFormField, value: string) {
    setValues((prev) => ({ ...prev, [field]: value }));
    // Clear a field's error as soon as it is edited, so a corrected value does
    // not keep showing a stale complaint.
    setErrors((prev) => (prev[field] ? { ...prev, [field]: undefined } : prev));
  }

  async function handleSave() {
    const nextErrors = validateProductForm(values);
    if (hasErrors(nextErrors)) {
      setErrors(nextErrors);
      return;
    }

    if (!user) return;

    try {
      await createProduct.mutateAsync({
        user_id: user.id,
        ...toProductFields(values),
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
        // The cap is enforced by the database, so this is the authoritative
        // signal — the sheet explains it rather than a bare alert.
        setShowUpgrade(true);
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

      <ProductForm
        values={values}
        errors={errors}
        onChange={handleChange}
        currency={currency}
        disabled={createProduct.isPending}
      />

      <UpgradeSheet
        visible={showUpgrade}
        onDismiss={() => setShowUpgrade(false)}
        limit={quota?.limit ?? null}
      />

      <View style={styles.actions}>
        <Button label="Save to vault" onPress={handleSave} loading={createProduct.isPending} />
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
