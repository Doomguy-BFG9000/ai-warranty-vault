import { TextField } from '@/components/TextField';
import type {
  ProductFormErrors,
  ProductFormField,
  ProductFormValues,
} from '@/lib/productForm';

interface ProductFormProps {
  values: ProductFormValues;
  errors: ProductFormErrors;
  onChange: (field: ProductFormField, value: string) => void;
  /** Shown alongside the price label when the receipt named a currency. */
  currency?: string | null;
  disabled?: boolean;
}

/**
 * The editable product fields, shared by the post-capture review screen and the
 * edit screen so the two cannot drift apart in labelling or validation hints.
 */
export function ProductForm({
  values,
  errors,
  onChange,
  currency,
  disabled = false,
}: ProductFormProps) {
  return (
    <>
      <TextField
        label="Product"
        value={values.name}
        onChangeText={(value) => onChange('name', value)}
        error={errors.name}
        placeholder="e.g. MacBook Pro 14”"
        autoCapitalize="words"
        editable={!disabled}
      />

      <TextField
        label="Store"
        value={values.merchant}
        onChangeText={(value) => onChange('merchant', value)}
        placeholder="e.g. Apple Store"
        autoCapitalize="words"
        editable={!disabled}
      />

      <TextField
        label="Purchase date"
        value={values.purchaseDate}
        onChangeText={(value) => onChange('purchaseDate', value)}
        error={errors.purchaseDate}
        hint="Format: YYYY-MM-DD"
        placeholder="2026-08-07"
        keyboardType="numbers-and-punctuation"
        autoCapitalize="none"
        editable={!disabled}
      />

      <TextField
        label="Warranty length (months)"
        value={values.warrantyMonths}
        onChangeText={(value) => onChange('warrantyMonths', value)}
        error={errors.warrantyMonths}
        hint="Expiry is calculated from the purchase date."
        placeholder="24"
        keyboardType="number-pad"
        editable={!disabled}
      />

      <TextField
        label={currency ? `Price (${currency})` : 'Price'}
        value={values.price}
        onChangeText={(value) => onChange('price', value)}
        error={errors.price}
        placeholder="1299.00"
        keyboardType="decimal-pad"
        editable={!disabled}
      />
    </>
  );
}
