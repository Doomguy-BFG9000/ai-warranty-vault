import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { useAuth } from '@/hooks/useAuth';
import { profileKeys } from '@/hooks/useProfile';
import { registerForPushNotifications } from '@/lib/notifications';
import { supabase } from '@/lib/supabase';

/**
 * Registers this device for push once per signed-in session and stores the
 * token on the profile, which is where the send-reminders cron looks for it.
 *
 * Deliberately silent: a user who declines notifications should not be nagged,
 * and local reminders still work without a token.
 */
export function usePushRegistration() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const registeredFor = useRef<string | null>(null);

  useEffect(() => {
    if (!user || registeredFor.current === user.id) return;
    registeredFor.current = user.id;

    let cancelled = false;

    void (async () => {
      const { token } = await registerForPushNotifications();
      if (cancelled || !token) return;

      const { error } = await supabase
        .from('profiles')
        .update({ expo_push_token: token })
        .eq('id', user.id);

      if (!error) {
        void queryClient.invalidateQueries({ queryKey: profileKeys.detail(user.id) });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [user, queryClient]);
}
