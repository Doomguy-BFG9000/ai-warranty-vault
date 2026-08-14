import { useQuery } from '@tanstack/react-query';

import { useAuth } from '@/hooks/useAuth';
import { useProfile } from '@/hooks/useProfile';
import { supabase } from '@/lib/supabase';

export interface ProductQuota {
  used: number;
  /** Null for premium accounts, which have no cap. */
  limit: number | null;
  isPremium: boolean;
  remaining: number | null;
  atLimit: boolean;
}

/**
 * How much of the free plan the user has consumed.
 *
 * The limit comes from `free_tier_product_limit()` rather than a constant here,
 * so the number the upgrade sheet quotes is the same one the database enforces.
 * This is presentation only — the cap itself is a trigger, so an out-of-date
 * client cannot spend more than its quota.
 */
export function useProductQuota() {
  const { user } = useAuth();
  const { data: profile } = useProfile();

  return useQuery({
    queryKey: ['product-quota', user?.id, profile?.is_premium],
    enabled: !!user && !!profile,
    queryFn: async (): Promise<ProductQuota> => {
      const isPremium = profile?.is_premium ?? false;

      const { count, error } = await supabase
        .from('products')
        .select('id', { count: 'exact', head: true });

      if (error) throw error;
      const used = count ?? 0;

      if (isPremium) {
        return { used, limit: null, isPremium: true, remaining: null, atLimit: false };
      }

      const { data: limit, error: limitError } = await supabase.rpc(
        'free_tier_product_limit',
      );
      if (limitError) throw limitError;

      return {
        used,
        limit,
        isPremium: false,
        remaining: Math.max(0, limit - used),
        atLimit: used >= limit,
      };
    },
  });
}
