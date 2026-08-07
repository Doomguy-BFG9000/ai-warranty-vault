import * as ImagePicker from 'expo-image-picker';
import { useCallback, useState } from 'react';
import { Alert, Linking } from 'react-native';

import { prepareReceiptImage } from '@/lib/receipts';

export type CaptureSource = 'camera' | 'library';

/**
 * Captures a receipt photo and returns a downscaled local URI ready to upload.
 * Returns null when the user cancels or declines permission.
 */
export function useReceiptCapture() {
  const [preparing, setPreparing] = useState(false);

  const capture = useCallback(async (source: CaptureSource): Promise<string | null> => {
    const granted = await ensurePermission(source);
    if (!granted) return null;

    const options: ImagePicker.ImagePickerOptions = {
      mediaTypes: ['images'],
      // Deliberately no editing step: iOS forces a square crop, which lops the
      // header and footer off a tall receipt — exactly where the store name and
      // warranty terms live. Keep the whole image and let the user retake if
      // the framing is poor.
      allowsEditing: false,
      quality: 1,
      exif: false,
    };

    const result =
      source === 'camera'
        ? await ImagePicker.launchCameraAsync(options)
        : await ImagePicker.launchImageLibraryAsync(options);

    if (result.canceled || !result.assets?.[0]) return null;

    const asset = result.assets[0];

    setPreparing(true);
    try {
      const prepared = await prepareReceiptImage(asset.uri, asset.width);
      return prepared.uri;
    } finally {
      setPreparing(false);
    }
  }, []);

  return { capture, preparing };
}

async function ensurePermission(source: CaptureSource): Promise<boolean> {
  const { status, canAskAgain } =
    source === 'camera'
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();

  if (status === 'granted') return true;

  // Once permission is permanently denied the OS stops showing the prompt, so
  // the only way forward is Settings — say so instead of silently doing nothing.
  const subject = source === 'camera' ? 'Camera' : 'Photo library';
  if (!canAskAgain) {
    Alert.alert(
      `${subject} access needed`,
      `Enable ${subject.toLowerCase()} access in Settings to add receipts.`,
      [
        { text: 'Not now', style: 'cancel' },
        { text: 'Open Settings', onPress: () => void Linking.openSettings() },
      ],
    );
  }

  return false;
}
