// Placeholder client config so modules that read it at import time do not warn.
// Tests never reach a network: these values only satisfy `src/lib/env.ts`.
process.env.EXPO_PUBLIC_SUPABASE_URL ??= 'http://127.0.0.1:54321';
process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ??= 'test-anon-key';
