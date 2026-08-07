import * as ImageManipulator from 'expo-image-manipulator';

import { supabase } from '@/lib/supabase';

export const RECEIPTS_BUCKET = 'receipts';

/**
 * Receipts are photographed, not scanned, so originals are frequently 8–12 MP.
 * Downscaling before upload keeps uploads fast on mobile data and stays well
 * under the bucket's 10 MB limit, while leaving enough resolution for the
 * vision model to read printed receipt text.
 */
const MAX_EDGE_PX = 1600;
const JPEG_QUALITY = 0.8;

export interface PreparedImage {
  uri: string;
  width: number;
  height: number;
}

/**
 * Downscales and re-encodes a captured image to a consistent JPEG.
 *
 * `sourceWidth` lets this skip resizing images that are already small. Without
 * it a modest photo gets scaled *up* to MAX_EDGE_PX, inflating the upload while
 * adding no detail the model can use.
 */
export async function prepareReceiptImage(
  uri: string,
  sourceWidth?: number,
): Promise<PreparedImage> {
  const context = ImageManipulator.ImageManipulator.manipulate(uri);

  if (sourceWidth === undefined || sourceWidth > MAX_EDGE_PX) {
    context.resize({ width: MAX_EDGE_PX });
  }

  const image = await context.renderAsync();
  const result = await image.saveAsync({
    compress: JPEG_QUALITY,
    format: ImageManipulator.SaveFormat.JPEG,
  });

  return { uri: result.uri, width: result.width, height: result.height };
}

/**
 * Uploads a prepared receipt to the private bucket.
 *
 * The `{userId}/` prefix is not cosmetic: the storage RLS policies authorize on
 * the first path segment, so a mismatch here is rejected by the server.
 */
export async function uploadReceiptImage(
  userId: string,
  imageUri: string,
): Promise<string> {
  const path = `${userId}/${Date.now()}-${Math.random().toString(36).slice(2, 10)}.jpg`;

  // React Native's fetch produces a Blob with no reliable arrayBuffer(), so the
  // bytes are read via FileReader-free base64-less path: fetch + arrayBuffer
  // works in Expo SDK 57's Hermes runtime.
  const response = await fetch(imageUri);
  const bytes = await response.arrayBuffer();

  const { error } = await supabase.storage
    .from(RECEIPTS_BUCKET)
    .upload(path, bytes, { contentType: 'image/jpeg', upsert: false });

  if (error) throw error;
  return path;
}

/**
 * Short-lived signed URL for displaying a stored receipt. The bucket is private,
 * so there is no public URL to fall back on.
 */
export async function getReceiptSignedUrl(
  path: string,
  expiresInSeconds = 3600,
): Promise<string> {
  const { data, error } = await supabase.storage
    .from(RECEIPTS_BUCKET)
    .createSignedUrl(path, expiresInSeconds);

  if (error) throw error;
  return data.signedUrl;
}

/** Removes a receipt image, e.g. when its product is deleted. */
export async function deleteReceiptImage(path: string): Promise<void> {
  const { error } = await supabase.storage.from(RECEIPTS_BUCKET).remove([path]);
  if (error) throw error;
}
