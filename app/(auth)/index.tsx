import { Link } from 'expo-router';
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { Screen } from '@/components/Screen';
import { ShieldLogo } from '@/components/ShieldLogo';
import { TextField } from '@/components/TextField';
import { useAuth } from '@/hooks/useAuth';
import { getAuthErrorMessage } from '@/lib/errors';
import { validateEmail } from '@/lib/validation';
import { colors, spacing, typography } from '@/theme';

export default function SignInScreen() {
  const { signIn } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  /**
   * Clears a field's error as soon as the user edits it. Without this the
   * "required" message stays on screen while they type a perfectly valid value,
   * which reads as the form being broken.
   */
  function editField(field: 'email' | 'password', value: string) {
    if (field === 'email') setEmail(value);
    else setPassword(value);

    if (errors[field]) setErrors((prev) => ({ ...prev, [field]: undefined }));
    if (formError) setFormError(null);
  }

  async function handleSubmit() {
    const emailError = validateEmail(email);
    const passwordError = password ? null : 'Password is required.';

    if (emailError || passwordError) {
      setErrors({ email: emailError ?? undefined, password: passwordError ?? undefined });
      return;
    }

    setErrors({});
    setFormError(null);
    setSubmitting(true);

    try {
      await signIn(email, password);
      // No navigation here: the root layout swaps route trees once the session
      // lands, so pushing a route would race that switch.
    } catch (error) {
      setFormError(getAuthErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Screen scroll keyboardAvoiding contentStyle={styles.content}>
      <View style={styles.header}>
        <ShieldLogo size={64} />
        <Text style={styles.title}>AI Warranty Vault</Text>
        <Text style={styles.subtitle}>
          Snap a receipt. Never miss a warranty again.
        </Text>
      </View>

      <View style={styles.form}>
        <TextField
          label="Email"
          value={email}
          onChangeText={(value) => editField('email', value)}
          error={errors.email}
          autoCapitalize="none"
          autoComplete="email"
          keyboardType="email-address"
          textContentType="emailAddress"
          placeholder="you@example.com"
          editable={!submitting}
        />

        <TextField
          label="Password"
          value={password}
          onChangeText={(value) => editField('password', value)}
          error={errors.password}
          secureTextEntry
          autoCapitalize="none"
          autoComplete="current-password"
          textContentType="password"
          placeholder="••••••••"
          editable={!submitting}
          onSubmitEditing={handleSubmit}
          returnKeyType="go"
        />

        {formError ? <Text style={styles.formError}>{formError}</Text> : null}

        <Button label="Sign in" onPress={handleSubmit} loading={submitting} />
      </View>

      <View style={styles.footer}>
        <Text style={styles.footerText}>New here?</Text>
        <Link href="/sign-up" style={styles.link}>
          Create an account
        </Link>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { flexGrow: 1, justifyContent: 'center', gap: spacing.xxl },
  header: { alignItems: 'center', gap: spacing.sm },
  title: { ...typography.title, color: colors.text },
  subtitle: {
    ...typography.body,
    color: colors.textMuted,
    textAlign: 'center',
    maxWidth: 280,
  },
  form: { gap: spacing.lg },
  formError: { ...typography.caption, color: colors.danger, textAlign: 'center' },
  footer: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    gap: spacing.xs,
  },
  footerText: { ...typography.body, color: colors.textMuted },
  link: { ...typography.subheading, color: colors.primary },
});
