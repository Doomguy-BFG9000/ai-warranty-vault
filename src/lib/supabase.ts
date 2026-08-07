import 'react-native-url-polyfill/auto';

import { createClient } from '@supabase/supabase-js';
import { AppState, Platform } from 'react-native';

import { env } from '@/lib/env';
import { secureStorageAdapter } from '@/lib/secureStorage';
import type { Database } from '@/types/database';

/**
 * Supabase client, typed against the generated database schema.
 *
 * Only the anon key is used here. It is safe to ship in the app bundle because
 * Row Level Security is what actually authorizes access — see
 * supabase/migrations/20260807000002_row_level_security.sql. The service-role
 * key and ANTHROPIC_API_KEY exist only inside Edge Functions.
 */
export const supabase = createClient<Database>(env.supabaseUrl, env.supabaseAnonKey, {
  auth: {
    storage: secureStorageAdapter,
    autoRefreshToken: true,
    persistSession: true,
    // No URL-based session detection outside the browser; on native the session
    // comes from storage, not a redirect fragment.
    detectSessionInUrl: false,
  },
});

/**
 * Supabase refreshes tokens on a timer, which the OS suspends in the
 * background. Tying the refresh loop to foreground state keeps sessions alive
 * without burning cycles while the app is backgrounded.
 */
if (Platform.OS !== 'web') {
  AppState.addEventListener('change', (state) => {
    if (state === 'active') {
      void supabase.auth.startAutoRefresh();
    } else {
      void supabase.auth.stopAutoRefresh();
    }
  });
}
