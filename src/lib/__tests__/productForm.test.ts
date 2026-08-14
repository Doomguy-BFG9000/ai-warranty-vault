import {
  EMPTY_PRODUCT_FORM,
  hasErrors,
  productToFormValues,
  toProductFields,
  validateProductForm,
} from '@/lib/productForm';
import type { Product } from '@/types/models';

const product = {
  id: 'p1',
  user_id: 'u1',
  name: 'Sony WH-1000XM6',
  merchant: 'TechWorld',
  category: 'electronics',
  price_cents: 42999,
  currency: 'USD',
  purchase_date: '2026-03-14',
  warranty_months: 24,
  warranty_expires_at: '2028-03-14',
  receipt_image_path: 'u1/receipt.jpg',
  extraction_status: 'done',
  notes: null,
  created_at: '2026-03-14T00:00:00Z',
  updated_at: '2026-03-14T00:00:00Z',
} as Product;

describe('validateProductForm', () => {
  it('requires only the product name', () => {
    const errors = validateProductForm(EMPTY_PRODUCT_FORM);
    expect(errors.name).toBeTruthy();
    // A receipt with an unreadable date or price is still worth keeping.
    expect(errors.purchaseDate).toBeUndefined();
    expect(errors.price).toBeUndefined();
    expect(errors.warrantyMonths).toBeUndefined();
  });

  it('omits keys for valid fields so a non-empty object means invalid', () => {
    const errors = validateProductForm({
      ...EMPTY_PRODUCT_FORM,
      name: 'Laptop',
    });
    expect(Object.keys(errors)).toHaveLength(0);
    expect(hasErrors(errors)).toBe(false);
  });

  it('surfaces per-field problems', () => {
    const errors = validateProductForm({
      name: 'Laptop',
      merchant: '',
      purchaseDate: '14/03/2026',
      warrantyMonths: '12.5',
      price: 'free',
    });
    expect(hasErrors(errors)).toBe(true);
    expect(errors.purchaseDate).toBeTruthy();
    expect(errors.warrantyMonths).toBeTruthy();
    expect(errors.price).toBeTruthy();
  });
});

describe('toProductFields', () => {
  it('trims text and converts numbers to their stored form', () => {
    expect(
      toProductFields({
        name: '  Sony WH-1000XM6  ',
        merchant: '  TechWorld  ',
        purchaseDate: '2026-03-14',
        warrantyMonths: '24',
        price: '429.99',
      }),
    ).toEqual({
      name: 'Sony WH-1000XM6',
      merchant: 'TechWorld',
      purchase_date: '2026-03-14',
      warranty_months: 24,
      price_cents: 42999,
    });
  });

  it('maps blank optional fields to null rather than empty strings', () => {
    // The columns are nullable; empty strings would fail the date parse and
    // show up as a bogus merchant.
    expect(toProductFields({ ...EMPTY_PRODUCT_FORM, name: 'Kettle' })).toEqual({
      name: 'Kettle',
      merchant: null,
      purchase_date: null,
      warranty_months: null,
      price_cents: null,
    });
  });
});

describe('productToFormValues', () => {
  it('round-trips a stored product back through the form', () => {
    const values = productToFormValues(product);
    expect(values).toEqual({
      name: 'Sony WH-1000XM6',
      merchant: 'TechWorld',
      purchaseDate: '2026-03-14',
      warrantyMonths: '24',
      price: '429.99',
    });

    const fields = toProductFields(values);
    expect(fields.price_cents).toBe(product.price_cents);
    expect(fields.warranty_months).toBe(product.warranty_months);
    expect(fields.purchase_date).toBe(product.purchase_date);
  });

  it('renders missing values as empty inputs, not the string "null"', () => {
    const values = productToFormValues({
      ...product,
      merchant: null,
      purchase_date: null,
      warranty_months: null,
      price_cents: null,
    } as Product);

    expect(values.merchant).toBe('');
    expect(values.purchaseDate).toBe('');
    expect(values.warrantyMonths).toBe('');
    expect(values.price).toBe('');
  });
});
