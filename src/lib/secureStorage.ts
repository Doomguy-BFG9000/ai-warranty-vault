import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

/**
 * Storage adapter for the Supabase auth session.
 *
 * The session contains access + refresh tokens, so it belongs in the device
 * keychain rather than plain AsyncStorage. SecureStore rejects values over
 * 2048 bytes, and a Supabase session comfortably exceeds that once the user
 * object is included, so values are transparently split across numbered
 * chunks with a small manifest under the original key.
 *
 * SecureStore has no web implementation; on web we fall back to AsyncStorage
 * (localStorage), which is the standard behaviour for Supabase on the web.
 */

const CHUNK_SIZE = 1536; // bytes of headroom under the 2048 limit
const MANIFEST_PREFIX = 'chunks:';

const isWeb = Platform.OS === 'web';

function chunkKey(key: string, index: number) {
  return `${key}.${index}`;
}

async function clearChunks(key: string, count: number) {
  const deletions = [];
  for (let i = 0; i < count; i++) {
    deletions.push(SecureStore.deleteItemAsync(chunkKey(key, i)));
  }
  await Promise.all(deletions);
}

/** Reads the chunk count previously written for `key`, or 0 if unchunked. */
async function readChunkCount(key: string): Promise<number> {
  const manifest = await SecureStore.getItemAsync(key);
  if (!manifest?.startsWith(MANIFEST_PREFIX)) return 0;
  const count = Number.parseInt(manifest.slice(MANIFEST_PREFIX.length), 10);
  return Number.isFinite(count) && count > 0 ? count : 0;
}

export const secureStorageAdapter = {
  async getItem(key: string): Promise<string | null> {
    if (isWeb) return AsyncStorage.getItem(key);

    const manifest = await SecureStore.getItemAsync(key);
    if (manifest === null) return null;

    // Value was small enough to store directly.
    if (!manifest.startsWith(MANIFEST_PREFIX)) return manifest;

    const count = await readChunkCount(key);
    if (count === 0) return null;

    const parts = await Promise.all(
      Array.from({ length: count }, (_, i) => SecureStore.getItemAsync(chunkKey(key, i))),
    );

    // A missing chunk means the stored value is corrupt; treat it as absent so
    // the user is simply asked to sign in again rather than hitting a crash.
    if (parts.some((part) => part === null)) {
      await this.removeItem(key);
      return null;
    }

    return parts.join('');
  },

  async setItem(key: string, value: string): Promise<void> {
    if (isWeb) return AsyncStorage.setItem(key, value);

    // Always clear previous chunks first, otherwise shrinking a value would
    // leave orphaned fragments behind.
    const previousCount = await readChunkCount(key);
    if (previousCount > 0) await clearChunks(key, previousCount);

    if (value.length <= CHUNK_SIZE) {
      await SecureStore.setItemAsync(key, value);
      return;
    }

    const chunks: string[] = [];
    for (let i = 0; i < value.length; i += CHUNK_SIZE) {
      chunks.push(value.slice(i, i + CHUNK_SIZE));
    }

    await Promise.all(
      chunks.map((chunk, i) => SecureStore.setItemAsync(chunkKey(key, i), chunk)),
    );
    await SecureStore.setItemAsync(key, `${MANIFEST_PREFIX}${chunks.length}`);
  },

  async removeItem(key: string): Promise<void> {
    if (isWeb) return AsyncStorage.removeItem(key);

    const count = await readChunkCount(key);
    if (count > 0) await clearChunks(key, count);
    await SecureStore.deleteItemAsync(key);
  },
};
