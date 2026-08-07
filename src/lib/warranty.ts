import { differenceInCalendarDays, parseISO } from 'date-fns';

import { colors } from '@/theme';

/**
 * Warranty lifecycle state, used for the status chips in the vault and to
 * decide how urgently to present a product.
 */
export type WarrantyStatus = 'active' | 'expiring' | 'expired' | 'unknown';

/** A warranty within this many days of lapsing is surfaced as "expiring soon". */
export const EXPIRING_SOON_DAYS = 30;

export interface WarrantyState {
  status: WarrantyStatus;
  /** Days until expiry; negative once expired, null when the date is unknown. */
  daysRemaining: number | null;
  label: string;
  color: string;
}

/**
 * Derives warranty state from the stored expiry date.
 *
 * `expiresAt` is a Postgres `date` ("YYYY-MM-DD"), compared on calendar days so
 * a warranty lapsing today reads as expiring rather than expired regardless of
 * the current time of day.
 */
export function getWarrantyState(
  expiresAt: string | null,
  now: Date = new Date(),
): WarrantyState {
  if (!expiresAt) {
    return {
      status: 'unknown',
      daysRemaining: null,
      label: 'No warranty date',
      color: colors.textFaint,
    };
  }

  const daysRemaining = differenceInCalendarDays(parseISO(expiresAt), now);

  if (daysRemaining < 0) {
    return {
      status: 'expired',
      daysRemaining,
      label: 'Expired',
      color: colors.danger,
    };
  }

  if (daysRemaining <= EXPIRING_SOON_DAYS) {
    return {
      status: 'expiring',
      daysRemaining,
      label: daysRemaining === 0 ? 'Expires today' : `${daysRemaining}d left`,
      color: colors.warning,
    };
  }

  return {
    status: 'active',
    daysRemaining,
    label: formatRemaining(daysRemaining),
    color: colors.success,
  };
}

/** Compact remaining-time label, e.g. "2y left", "8mo left", "45d left". */
function formatRemaining(days: number): string {
  if (days >= 365) {
    const years = Math.floor(days / 365);
    return `${years}y left`;
  }
  if (days >= 60) {
    const months = Math.floor(days / 30);
    return `${months}mo left`;
  }
  return `${days}d left`;
}

/** Formats minor currency units for display, e.g. 129900 -> "$1,299.00". */
export function formatPrice(priceCents: number | null, currency = 'USD'): string | null {
  if (priceCents === null) return null;
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
    }).format(priceCents / 100);
  } catch {
    // Unknown/invalid ISO code — fall back to a plain amount rather than crash.
    return (priceCents / 100).toFixed(2);
  }
}
