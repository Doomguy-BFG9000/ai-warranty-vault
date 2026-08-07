import { router } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Alert, Image, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { Screen } from '@/components/Screen';
import { useAuth } from '@/hooks/useAuth';
import { useReceiptCapture } from '@/hooks/useReceiptCapture';
import { getDatabaseErrorMessage } from '@/lib/errors';
import { uploadReceiptImage } from '@/lib/receipts';
import { colors, radii, spacing, typography } from '@/theme';

export default function AddScreen() {
  const { user } = useAuth();
  const { capture, preparing } = useReceiptCapture();

  const [imageUri, setImageUri] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  async function handleCapture(source: 'camera' | 'library') {
    try {
      const uri = await capture(source);
      if (uri) setImageUri(uri);
    } catch {
      Alert.alert('Could not open the camera', 'Please try again.');
    }
  }

  /**
   * Uploads the image, then hands the storage path to the review screen. The
   * product row is deliberately not created yet: a capture the user abandons
   * would otherwise sit in their vault and count against the free-tier limit.
   */
  async function handleContinue() {
    if (!imageUri || !user) return;

    setUploading(true);
    try {
      const path = await uploadReceiptImage(user.id, imageUri);
      setImageUri(null);
      router.push({ pathname: '/review', params: { receiptPath: path } });
    } catch (error) {
      Alert.alert('Upload failed', getDatabaseErrorMessage(error));
    } finally {
      setUploading(false);
    }
  }

  const busy = preparing || uploading;

  if (imageUri) {
    return (
      <Screen edges={['bottom']} contentStyle={styles.previewScreen}>
        <Image source={{ uri: imageUri }} style={styles.preview} resizeMode="contain" />
        <View style={styles.actions}>
          <Button
            label={uploading ? 'Uploading…' : 'Use this receipt'}
            onPress={handleContinue}
            loading={uploading}
          />
          <Button
            label="Retake"
            variant="secondary"
            onPress={() => setImageUri(null)}
            disabled={busy}
          />
        </View>
      </Screen>
    );
  }

  return (
    <Screen edges={['bottom']} contentStyle={styles.centered}>
      {preparing ? (
        <>
          <ActivityIndicator color={colors.primary} size="large" />
          <Text style={styles.subtitle}>Preparing image…</Text>
        </>
      ) : (
        <>
          <Text style={styles.emoji}>📸</Text>
          <Text style={styles.title}>Add a receipt</Text>
          <Text style={styles.subtitle}>
            Photograph the receipt and we&apos;ll pull out the product and warranty
            details for you to confirm.
          </Text>

          <View style={styles.actions}>
            <Button label="Take a photo" onPress={() => handleCapture('camera')} />
            <Button
              label="Choose from library"
              variant="secondary"
              onPress={() => handleCapture('library')}
            />
          </View>
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  previewScreen: { flex: 1, gap: spacing.lg },
  emoji: { fontSize: 56, marginBottom: spacing.sm },
  title: { ...typography.heading, color: colors.text },
  subtitle: {
    ...typography.body,
    color: colors.textMuted,
    textAlign: 'center',
    maxWidth: 300,
    lineHeight: 22,
  },
  preview: {
    flex: 1,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
  },
  actions: { width: '100%', gap: spacing.md, marginTop: spacing.lg },
});
