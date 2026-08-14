import { Stack, router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Alert, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { ProductForm } from '@/components/ProductForm';
import { Screen } from '@/components/Screen';
import { useProduct, useUpdateProduct } from '@/hooks/useProducts';
import { getDatabaseErrorMessage } from '@/lib/errors';
import {
  EMPTY_PRODUCT_FORM,
  hasErrors,
  productToFormValues,
  toProductFields,
  validateProductForm,
  type ProductFormErrors,
  type ProductFormField,
  type ProductFormValues,
} from '@/lib/productForm';
import { colors, spacing, typography } from '@/theme';

export default function EditProductScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: product, isLoading, isError, error } = useProduct(id);
  const updateProduct = useUpdateProduct();

  const [values, setValues] = useState<ProductFormValues>(EMPTY_PRODUCT_FORM);
  const [errors, setErrors] = useState<ProductFormErrors>({});
  const [loadedId, setLoadedId] = useState<string | null>(null);

  // Populate once per product, during render rather than in an effect: an
  // effect would commit an empty form first and then immediately re-render with
  // the real values. Comparing against the id we last loaded means a background
  // refetch cannot discard what the user has typed.
  if (product && product.id !== loadedId) {
    setValues(productToFormValues(product));
    setLoadedId(product.id);
  }

  function handleChange(field: ProductFormField, value: string) {
    setValues((prev) => ({ ...prev, [field]: value }));
    setErrors((prev) => (prev[field] ? { ...prev, [field]: undefined } : prev));
  }

  async function handleSave() {
    const nextErrors = validateProductForm(values);
    if (hasErrors(nextErrors)) {
      setErrors(nextErrors);
      return;
    }

    if (!product) return;

    try {
      await updateProduct.mutateAsync({
        id: product.id,
        changes: toProductFields(values),
      });
      router.back();
    } catch (err) {
      Alert.alert('Could not save', getDatabaseErrorMessage(err));
    }
  }

  if (isLoading) {
    return (
      <Screen contentStyle={styles.centered}>
        <Stack.Screen options={{ title: 'Edit' }} />
        <ActivityIndicator color={colors.primary} size="large" />
      </Screen>
    );
  }

  if (isError || !product) {
    return (
      <Screen contentStyle={styles.centered}>
        <Stack.Screen options={{ title: 'Edit' }} />
        <Text style={styles.title}>This product is not available</Text>
        <Text style={styles.muted}>{getDatabaseErrorMessage(error)}</Text>
        <Button label="Back" variant="secondary" onPress={() => router.back()} />
      </Screen>
    );
  }

  return (
    <Screen scroll keyboardAvoiding>
      <Stack.Screen options={{ title: 'Edit details' }} />

      <Text style={styles.hint}>
        Changing the purchase date or warranty length recalculates the expiry and
        reschedules the reminders.
      </Text>

      <ProductForm
        values={values}
        errors={errors}
        onChange={handleChange}
        currency={product.currency}
        disabled={updateProduct.isPending}
      />

      <View style={styles.actions}>
        <Button label="Save changes" onPress={handleSave} loading={updateProduct.isPending} />
        <Button
          label="Cancel"
          variant="ghost"
          onPress={() => router.back()}
          disabled={updateProduct.isPending}
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.md },
  title: { ...typography.heading, color: colors.text, textAlign: 'center' },
  muted: { ...typography.body, color: colors.textMuted, textAlign: 'center' },
  hint: { ...typography.caption, color: colors.textFaint, lineHeight: 18 },
  actions: { gap: spacing.sm, marginTop: spacing.md },
});
