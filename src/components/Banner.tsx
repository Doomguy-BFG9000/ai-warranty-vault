import { StyleSheet, Text, View } from 'react-native';

import { colors, radii, spacing, typography } from '@/theme';

type Tone = 'info' | 'warning' | 'danger' | 'success';

const TONE_COLORS: Record<Tone, string> = {
  info: colors.primary,
  warning: colors.warning,
  danger: colors.danger,
  success: colors.success,
};

/**
 * An inline notice tied to the thing it is about.
 *
 * Used for the states this app has to be honest about and that an Alert would bury:
 * a truncated scan, a document indexed under an old embedding space, a question the
 * documents cannot answer. The label is always words as well as colour.
 */
export function Banner({
  tone = 'info',
  title,
  message,
}: {
  tone?: Tone;
  title: string;
  message?: string | null;
}) {
  const accent = TONE_COLORS[tone];
  return (
    <View style={[styles.banner, { borderColor: accent, backgroundColor: `${accent}18` }]}>
      <Text style={[styles.title, { color: accent }]}>{title}</Text>
      {message ? <Text style={styles.message}>{message}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    gap: spacing.xs,
  },
  title: { ...typography.label },
  message: { ...typography.caption, color: colors.textMuted, lineHeight: 18 },
});
