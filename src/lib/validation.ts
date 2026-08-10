/** Minimum accepted by Supabase Auth by default. */
export const MIN_PASSWORD_LENGTH = 8;

// Deliberately permissive: the authoritative check is the confirmation email,
// so this only catches obvious typos rather than trying to model RFC 5322.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateEmail(email: string): string | null {
  const trimmed = email.trim();
  if (!trimmed) return 'Email is required.';
  if (!EMAIL_PATTERN.test(trimmed)) return 'Please enter a valid email address.';
  return null;
}

export function validatePassword(password: string): string | null {
  if (!password) return 'Password is required.';
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  return null;
}

const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Validates a purchase date in the `YYYY-MM-DD` form Postgres expects.
 * Empty is allowed — a receipt with an unreadable date is still worth keeping,
 * it just cannot have a computed expiry yet.
 */
export function validatePurchaseDate(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  if (!ISO_DATE_PATTERN.test(trimmed)) return 'Use the format YYYY-MM-DD.';

  // Date.parse accepts 2026-02-31 and rolls it over, so round-trip to confirm
  // the components survived unchanged.
  const parsed = new Date(`${trimmed}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return 'That date does not exist.';
  if (parsed.toISOString().slice(0, 10) !== trimmed) return 'That date does not exist.';

  // A purchase cannot be in the future; this is almost always a typo.
  const today = new Date();
  today.setUTCHours(23, 59, 59, 999);
  if (parsed > today) return 'Purchase date cannot be in the future.';

  return null;
}

/** Validates warranty length in months. Empty is allowed. */
export function validateWarrantyMonths(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  if (!/^\d+$/.test(trimmed)) return 'Enter a whole number of months.';

  const months = Number(trimmed);
  // Matches the products_warranty_months_sane database constraint.
  if (months > 600) return 'That is longer than 50 years.';

  return null;
}

/** Validates a price in major units (e.g. "129.99"). Empty is allowed. */
export function validatePrice(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  if (!/^\d+([.,]\d{1,2})?$/.test(trimmed)) return 'Enter an amount like 129.99.';
  return null;
}

/** Converts a validated price string to the integer minor units the DB stores. */
export function priceToCents(value: string): number | null {
  const trimmed = value.trim().replace(',', '.');
  if (!trimmed) return null;
  return Math.round(Number(trimmed) * 100);
}

/** Formats stored minor units back into an editable major-unit string. */
export function centsToPriceInput(cents: number | null): string {
  if (cents === null) return '';
  return (cents / 100).toFixed(2);
}

/**
 * Normalises a model-supplied currency code to the `^[A-Z]{3}$` shape the
 * products table enforces, returning null for anything that does not fit.
 *
 * Extracted values reach the database directly, so anything the model invents
 * ("dollars", "us", "usd ") would otherwise fail the insert with a constraint
 * violation the user can do nothing about. Falling back to null lets the column
 * default apply instead.
 */
export function normaliseCurrency(value: string | null | undefined): string | null {
  if (!value) return null;
  const code = value.trim().toUpperCase();
  return /^[A-Z]{3}$/.test(code) ? code : null;
}
