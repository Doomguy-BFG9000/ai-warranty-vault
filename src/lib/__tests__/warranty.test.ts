import { EXPIRING_SOON_DAYS, formatPrice, getWarrantyState } from '@/lib/warranty';

// Fixed "today" so the boundary cases stay deterministic. Expiry dates are
// compared in local calendar days, and jest.config.js pins TZ=UTC so these
// instants mean the same thing on every machine.
const NOW = new Date('2026-06-15T12:00:00Z');

describe('getWarrantyState', () => {
  it('reports unknown when there is no expiry date', () => {
    const state = getWarrantyState(null, NOW);
    expect(state.status).toBe('unknown');
    expect(state.daysRemaining).toBeNull();
  });

  it('reports active for a warranty comfortably in the future', () => {
    const state = getWarrantyState('2028-01-01', NOW);
    expect(state.status).toBe('active');
    expect(state.daysRemaining).toBeGreaterThan(EXPIRING_SOON_DAYS);
  });

  it('reports expired once the date has passed', () => {
    const state = getWarrantyState('2026-06-14', NOW);
    expect(state.status).toBe('expired');
    expect(state.daysRemaining).toBe(-1);
    expect(state.label).toBe('Expired');
  });

  it('treats the expiry day itself as expiring, not expired', () => {
    const state = getWarrantyState('2026-06-15', NOW);
    expect(state.status).toBe('expiring');
    expect(state.daysRemaining).toBe(0);
    expect(state.label).toBe('Expires today');
  });

  it('includes the last day of the expiring-soon window', () => {
    const state = getWarrantyState('2026-07-15', NOW); // exactly 30 days out
    expect(state.daysRemaining).toBe(EXPIRING_SOON_DAYS);
    expect(state.status).toBe('expiring');
  });

  it('flips to active one day past the expiring-soon window', () => {
    const state = getWarrantyState('2026-07-16', NOW); // 31 days out
    expect(state.status).toBe('active');
  });

  it('ignores time of day so late-evening checks do not expire early', () => {
    const lateEvening = new Date('2026-06-15T23:59:00Z');
    expect(getWarrantyState('2026-06-15', lateEvening).status).toBe('expiring');
  });

  it('summarises long remaining periods in years and months', () => {
    expect(getWarrantyState('2028-06-15', NOW).label).toMatch(/y left$/);
    expect(getWarrantyState('2026-10-15', NOW).label).toMatch(/mo left$/);
  });
});

describe('formatPrice', () => {
  it('returns null when no price is recorded', () => {
    expect(formatPrice(null)).toBeNull();
  });

  it('formats minor units as a currency amount', () => {
    expect(formatPrice(129900, 'USD')).toContain('1,299.00');
  });

  it('formats zero rather than treating it as missing', () => {
    expect(formatPrice(0, 'USD')).toContain('0.00');
  });

  it('falls back to a plain amount for an unknown currency code', () => {
    expect(formatPrice(1050, 'XXXXX')).toBe('10.50');
  });
});
