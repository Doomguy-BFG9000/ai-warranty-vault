import {
  centsToPriceInput,
  normaliseCurrency,
  priceToCents,
  validateEmail,
  validatePassword,
  validatePrice,
  validatePurchaseDate,
  validateWarrantyMonths,
} from '@/lib/validation';

describe('validateEmail', () => {
  it('rejects an empty value', () => {
    expect(validateEmail('  ')).toBe('Email is required.');
  });

  it('rejects addresses without a domain or @', () => {
    expect(validateEmail('nope')).toBeTruthy();
    expect(validateEmail('nope@nodot')).toBeTruthy();
  });

  it('accepts a normal address, ignoring surrounding whitespace', () => {
    expect(validateEmail('  greg@example.com  ')).toBeNull();
  });
});

describe('validatePassword', () => {
  it('requires the minimum length', () => {
    expect(validatePassword('short1!')).toBeTruthy();
    expect(validatePassword('longenough1!')).toBeNull();
  });
});

describe('validatePurchaseDate', () => {
  it('treats an empty date as acceptable', () => {
    // A receipt with an unreadable date is still worth keeping.
    expect(validatePurchaseDate('')).toBeNull();
  });

  it('requires the ISO format', () => {
    expect(validatePurchaseDate('07/08/2026')).toBe('Use the format YYYY-MM-DD.');
  });

  it('rejects a day that does not exist rather than rolling it over', () => {
    expect(validatePurchaseDate('2026-02-31')).toBe('That date does not exist.');
  });

  it('rejects Feb 29 in a non-leap year but accepts it in a leap year', () => {
    expect(validatePurchaseDate('2026-02-29')).toBe('That date does not exist.');
    expect(validatePurchaseDate('2024-02-29')).toBeNull();
  });

  it('rejects a future purchase date', () => {
    const nextYear = new Date();
    nextYear.setFullYear(nextYear.getFullYear() + 1);
    expect(validatePurchaseDate(nextYear.toISOString().slice(0, 10))).toBe(
      'Purchase date cannot be in the future.',
    );
  });

  it('accepts today', () => {
    expect(validatePurchaseDate(new Date().toISOString().slice(0, 10))).toBeNull();
  });
});

describe('validateWarrantyMonths', () => {
  it('allows an empty value', () => {
    expect(validateWarrantyMonths('')).toBeNull();
  });

  it('rejects non-integers', () => {
    expect(validateWarrantyMonths('12.5')).toBeTruthy();
    expect(validateWarrantyMonths('-3')).toBeTruthy();
  });

  it('mirrors the database ceiling of 600 months', () => {
    expect(validateWarrantyMonths('600')).toBeNull();
    expect(validateWarrantyMonths('601')).toBeTruthy();
  });
});

describe('price handling', () => {
  it('rejects malformed amounts', () => {
    expect(validatePrice('12.345')).toBeTruthy();
    expect(validatePrice('abc')).toBeTruthy();
  });

  it('accepts comma or dot decimal separators', () => {
    expect(validatePrice('129,99')).toBeNull();
    expect(validatePrice('129.99')).toBeNull();
  });

  it('converts to integer minor units without float drift', () => {
    expect(priceToCents('129.99')).toBe(12999);
    expect(priceToCents('129,99')).toBe(12999);
    // 1.005 * 100 is 100.49999... in binary floating point.
    expect(priceToCents('10.10')).toBe(1010);
  });

  it('round-trips through the editable string form', () => {
    expect(centsToPriceInput(priceToCents('129.99'))).toBe('129.99');
    expect(centsToPriceInput(null)).toBe('');
    expect(centsToPriceInput(0)).toBe('0.00');
  });
});

describe('normaliseCurrency', () => {
  it('accepts a well-formed code', () => {
    expect(normaliseCurrency('USD')).toBe('USD');
  });

  it('upper-cases and trims what the model returns', () => {
    expect(normaliseCurrency(' usd ')).toBe('USD');
  });

  it('rejects anything the database constraint would reject', () => {
    // These reach a NOT NULL column with a ^[A-Z]{3}$ check, so a bad value
    // would fail the insert rather than the extraction.
    expect(normaliseCurrency('dollars')).toBeNull();
    expect(normaliseCurrency('US')).toBeNull();
    expect(normaliseCurrency('US$')).toBeNull();
    expect(normaliseCurrency('')).toBeNull();
    expect(normaliseCurrency(null)).toBeNull();
    expect(normaliseCurrency(undefined)).toBeNull();
  });
});
