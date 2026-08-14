import {
  centsToPriceInput,
  priceToCents,
  validatePrice,
  validatePurchaseDate,
  validateWarrantyMonths,
} from '@/lib/validation';
import type { Product, ProductInsert } from '@/types/models';

/**
 * The editable shape of a product, shared by the post-capture review screen and
 * the edit screen so both validate and serialise identically.
 *
 * Values are kept as strings because that is what the inputs hold; conversion to
 * the database representation happens once, in `toProductFields`.
 */
export interface ProductFormValues {
  name: string;
  merchant: string;
  purchaseDate: string;
  warrantyMonths: string;
  price: string;
}

export type ProductFormField = keyof ProductFormValues;

export type ProductFormErrors = Partial<Record<ProductFormField, string>>;

export const EMPTY_PRODUCT_FORM: ProductFormValues = {
  name: '',
  merchant: '',
  purchaseDate: '',
  warrantyMonths: '',
  price: '',
};

/** Fills the form from a stored product, for the edit screen. */
export function productToFormValues(product: Product): ProductFormValues {
  return {
    name: product.name,
    merchant: product.merchant ?? '',
    purchaseDate: product.purchase_date ?? '',
    warrantyMonths: product.warranty_months === null ? '' : String(product.warranty_months),
    price: centsToPriceInput(product.price_cents),
  };
}

/**
 * Validates the whole form. Only `name` is mandatory — a receipt with an
 * unreadable date or price is still worth keeping, it just cannot have a
 * computed expiry until the user fills the gap in.
 */
export function validateProductForm(values: ProductFormValues): ProductFormErrors {
  const errors: ProductFormErrors = {
    name: values.name.trim() ? undefined : 'Product name is required.',
    purchaseDate: validatePurchaseDate(values.purchaseDate) ?? undefined,
    warrantyMonths: validateWarrantyMonths(values.warrantyMonths) ?? undefined,
    price: validatePrice(values.price) ?? undefined,
  };

  // Drop the undefined entries so callers can treat a non-empty object as "invalid".
  for (const key of Object.keys(errors) as ProductFormField[]) {
    if (!errors[key]) delete errors[key];
  }

  return errors;
}

export function hasErrors(errors: ProductFormErrors): boolean {
  return Object.keys(errors).length > 0;
}

/** Converts validated form values into the columns the products table expects. */
export function toProductFields(
  values: ProductFormValues,
): Pick<
  ProductInsert,
  'name' | 'merchant' | 'purchase_date' | 'warranty_months' | 'price_cents'
> {
  return {
    name: values.name.trim(),
    merchant: values.merchant.trim() || null,
    purchase_date: values.purchaseDate.trim() || null,
    warranty_months: values.warrantyMonths.trim() ? Number(values.warrantyMonths.trim()) : null,
    price_cents: priceToCents(values.price),
  };
}
