/**
 * Runtime environment access.
 *
 * Public config is exposed to the app bundle via `EXPO_PUBLIC_*` variables,
 * which Expo inlines from the project `.env` file. These are NOT secrets:
 * the Supabase anon key is safe to ship because Row Level Security enforces
 * per-user access. The ANTHROPIC_API_KEY and Supabase service-role key live
 * ONLY in Edge Function environments and never appear here.
 */

const supabaseUrl = process.env.EXPO_PUBLIC_SUPABASE_URL;
const supabaseAnonKey = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY;

/**
 * Base URL of the document-assistant service (services/ai). A URL, not a secret:
 * every request to it carries the user's own Supabase access token, and the service
 * verifies that signature before it touches anything.
 *
 * On a simulator `http://127.0.0.1:8000` reaches the host. A physical device needs
 * the machine's LAN address, because localhost on the phone is the phone.
 */
const aiApiUrl = process.env.EXPO_PUBLIC_AI_API_URL;

if (!supabaseUrl || !supabaseAnonKey) {
  // Surfaced early so a missing .env is obvious in development.
  console.warn(
    '[env] Missing EXPO_PUBLIC_SUPABASE_URL / EXPO_PUBLIC_SUPABASE_ANON_KEY. ' +
      'Copy .env.example to .env and fill in your Supabase project values.',
  );
}

export const env = {
  supabaseUrl: supabaseUrl ?? '',
  supabaseAnonKey: supabaseAnonKey ?? '',
  isConfigured: Boolean(supabaseUrl && supabaseAnonKey),
  // Trailing slash trimmed once here so no call site has to think about it.
  aiApiUrl: (aiApiUrl ?? '').replace(/\/+$/, ''),
  // The document features are additive: with no AI service configured the vault,
  // receipt capture and reminders all work exactly as before, and the new screens
  // say what is missing instead of failing.
  hasAiApi: Boolean(aiApiUrl),
} as const;
