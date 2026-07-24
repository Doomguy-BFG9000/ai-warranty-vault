/**
 * Design tokens for AI Warranty Vault.
 * Single source of truth for colors, spacing, radii and typography so the UI
 * reads as one system. Kept intentionally small for MVP speed.
 */

export const colors = {
  // Brand
  primary: '#2563EB',
  primaryDark: '#1D4ED8',
  primarySoft: '#DBEAFE',

  // Surfaces
  background: '#0B1120',
  surface: '#111A2E',
  surfaceAlt: '#1B2740',
  border: '#25304A',

  // Text
  text: '#F8FAFC',
  textMuted: '#94A3B8',
  textFaint: '#64748B',

  // Status (warranty state)
  success: '#22C55E',
  warning: '#F59E0B',
  danger: '#EF4444',

  // On-color text
  onPrimary: '#FFFFFF',
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radii = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  pill: 999,
} as const;

export const typography = {
  title: { fontSize: 28, fontWeight: '700' as const },
  heading: { fontSize: 20, fontWeight: '700' as const },
  subheading: { fontSize: 16, fontWeight: '600' as const },
  body: { fontSize: 15, fontWeight: '400' as const },
  label: { fontSize: 13, fontWeight: '600' as const },
  caption: { fontSize: 12, fontWeight: '400' as const },
} as const;

export const theme = { colors, spacing, radii, typography };
export type Theme = typeof theme;
