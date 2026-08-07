import { router } from 'expo-router';
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { Screen } from '@/components/Screen';
import { TextField } from '@/components/TextField';
import { useAuth } from '@/hooks/useAuth';
import { getAuthErrorMessage } from '@/lib/errors';
import { MIN_PASSWORD_LENGTH, validateEmail, validatePassword } from '@/lib/validation';
import { colors, spacing, typography } from '@/theme';

export default function SignUpScreen() {
  const { signUp } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirmationSent, setConfirmationSent] = useState(false);

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
    const passwordError = validatePassword(password);

    if (emailError || passwordError) {
      setErrors({ email: emailError ?? undefined, password: passwordError ?? undefined });
      return;
    }

    setErrors({});
    setFormError(null);
    setSubmitting(true);

    try {
      const { needsEmailConfirmation } = await signUp(email, password);
      if (needsEmailConfirmation) {
        // No session yet — tell the user to confirm rather than leaving them on
        // a form that looks like it failed.
        setConfirmationSent(true);
      }
      // Otherwise the root layout swaps to the vault as soon as the session lands.
    } catch (error) {
      setFormError(getAuthErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  if (confirmationSent) {
    return (
      <Screen contentStyle={styles.centered}>
        <Text style={styles.logo}>📬</Text>
        <Text style={styles.title}>Check your inbox</Text>
        <Text style={styles.subtitle}>
          We sent a confirmation link to {email.trim()}. Tap it to activate your
          vault, then sign in.
        </Text>
        <Button label="Back to sign in" variant="secondary" onPress={() => router.back()} />
      </Screen>
    );
  }

  return (
    <Screen scroll keyboardAvoiding contentStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Create your vault</Text>
        <Text style={styles.subtitle}>
          Track up to 5 products free. No card required.
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
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
          secureTextEntry
          autoCapitalize="none"
          autoComplete="new-password"
          textContentType="newPassword"
          placeholder="••••••••"
          editable={!submitting}
          onSubmitEditing={handleSubmit}
          returnKeyType="go"
        />

        {formError ? <Text style={styles.formError}>{formError}</Text> : null}

        <Button label="Create account" onPress={handleSubmit} loading={submitting} />
      </View>

      <View style={styles.footer}>
        <Text style={styles.footerText}>Already have an account?</Text>
        <Text style={styles.link} onPress={() => router.back()}>
          Sign in
        </Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { flexGrow: 1, justifyContent: 'center', gap: spacing.xxl },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: spacing.lg },
  header: { alignItems: 'center', gap: spacing.sm },
  logo: { fontSize: 56 },
  title: { ...typography.title, color: colors.text, textAlign: 'center' },
  subtitle: {
    ...typography.body,
    color: colors.textMuted,
    textAlign: 'center',
    maxWidth: 300,
    lineHeight: 22,
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
