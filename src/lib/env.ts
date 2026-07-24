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
} as const;
