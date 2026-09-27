import * as DocumentPicker from 'expo-document-picker';

import { supabase } from '@/lib/supabase';

/**
 * Picking a document and getting it into the private bucket.
 *
 * The file goes straight from the device to Supabase Storage with the user's own
 * token — the same route a receipt photo takes — and only then is it registered with
 * the AI service. Two reasons: a 15 MB body never has to pass through the API, and
 * Storage RLS is what decides where a file may land, rather than a check in
 * application code.
 *
 * The object path is `{userId}/{productId}/{uuid}.{ext}`. That shape is load-bearing
 * at three levels: the storage policy authorises on the first segment, the API checks
 * both segments at registration, and a CHECK constraint on the row pins it to that
 * document's own user and product.
 */

export const DOCUMENTS_BUCKET = 'product-documents';

/** Mirrors the bucket's allowed_mime_types and the service's extractor. */
export const ACCEPTED_DOCUMENT_TYPES = [
  'application/pdf',
  'text/plain',
  'image/jpeg',
  'image/png',
  'image/webp',
] as const;

/** Mirrors the bucket's file_size_limit. Checked here so the user is told before the upload. */
export const MAX_DOCUMENT_BYTES = 15 * 1024 * 1024;

export interface PickedDocument {
  uri: string;
  name: string;
  mimeType: string;
  byteSize: number;
}

export class DocumentTooLargeError extends Error {
  constructor() {
    super(
      `That file is larger than ${Math.round(MAX_DOCUMENT_BYTES / (1024 * 1024))} MB. ` +
        'Try a smaller scan or split it up.',
    );
    this.name = 'DocumentTooLargeError';
  }
}

export class UnsupportedDocumentTypeError extends Error {
  constructor(mimeType: string) {
    super(`${mimeType || 'That file type'} is not supported. Use a PDF, a text file, or a photo.`);
    this.name = 'UnsupportedDocumentTypeError';
  }
}

/** Opens the system picker. Returns null when the user cancels. */
export async function pickDocument(): Promise<PickedDocument | null> {
  const result = await DocumentPicker.getDocumentAsync({
    type: [...ACCEPTED_DOCUMENT_TYPES],
    // Needed so the file can be read back for upload; the picker's original URI is
    // not guaranteed to be readable by the app on either platform.
    copyToCacheDirectory: true,
    multiple: false,
  });

  if (result.canceled || !result.assets?.[0]) return null;
  const asset = result.assets[0];

  // Some providers report no MIME type. Fall back to the extension rather than
  // rejecting a perfectly good PDF.
  const mimeType = asset.mimeType ?? guessMimeType(asset.name);
  if (!ACCEPTED_DOCUMENT_TYPES.includes(mimeType as (typeof ACCEPTED_DOCUMENT_TYPES)[number])) {
    throw new UnsupportedDocumentTypeError(mimeType);
  }
  if (asset.size !== undefined && asset.size > MAX_DOCUMENT_BYTES) {
    throw new DocumentTooLargeError();
  }

  return {
    uri: asset.uri,
    name: asset.name,
    mimeType,
    byteSize: asset.size ?? 0,
  };
}

export async function uploadProductDocument(
  userId: string,
  productId: string,
  document: PickedDocument,
): Promise<{ storagePath: string; byteSize: number }> {
  const extension = extensionFor(document.name, document.mimeType);
  const path = `${userId}/${productId}/${randomId()}.${extension}`;

  const response = await fetch(document.uri);
  const bytes = await response.arrayBuffer();

  // Checked again on the real byte count: a picker's reported size can be missing or
  // wrong, and the bucket would reject the upload with a far less helpful message.
  if (bytes.byteLength > MAX_DOCUMENT_BYTES) throw new DocumentTooLargeError();

  const { error } = await supabase.storage
    .from(DOCUMENTS_BUCKET)
    .upload(path, bytes, { contentType: document.mimeType, upsert: false });

  if (error) throw error;
  return { storagePath: path, byteSize: bytes.byteLength };
}

/** Short-lived signed URL for viewing the original. The bucket is private. */
export async function getDocumentSignedUrl(path: string, expiresInSeconds = 3600): Promise<string> {
  const { data, error } = await supabase.storage
    .from(DOCUMENTS_BUCKET)
    .createSignedUrl(path, expiresInSeconds);
  if (error) throw error;
  return data.signedUrl;
}

function guessMimeType(name: string): string {
  const extension = name.split('.').pop()?.toLowerCase();
  switch (extension) {
    case 'pdf':
      return 'application/pdf';
    case 'txt':
    case 'text':
    case 'md':
      return 'text/plain';
    case 'jpg':
    case 'jpeg':
      return 'image/jpeg';
    case 'png':
      return 'image/png';
    case 'webp':
      return 'image/webp';
    default:
      return '';
  }
}

function extensionFor(name: string, mimeType: string): string {
  const fromName = name.split('.').pop()?.toLowerCase();
  // Restricted to a known set because the extension ends up in the object path, which
  // the database validates against a character class.
  if (fromName && /^[a-z0-9]{1,5}$/.test(fromName)) return fromName;
  switch (mimeType) {
    case 'application/pdf':
      return 'pdf';
    case 'text/plain':
      return 'txt';
    case 'image/png':
      return 'png';
    case 'image/webp':
      return 'webp';
    default:
      return 'jpg';
  }
}

function randomId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
