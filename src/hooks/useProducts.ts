import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { useAuth } from '@/hooks/useAuth';
import { cancelLocalReminders, syncLocalReminders } from '@/lib/notifications';
import { deleteReceiptImage } from '@/lib/receipts';
import { supabase } from '@/lib/supabase';
import type { Product, ProductInsert, ProductUpdate } from '@/types/models';

export const productKeys = {
  all: ['products'] as const,
  list: (userId: string) => [...productKeys.all, 'list', userId] as const,
  detail: (id: string) => [...productKeys.all, 'detail', id] as const,
};

/**
 * All of the signed-in user's products, soonest expiry first so the items that
 * need attention surface at the top. Rows with no expiry date sort last.
 */
export function useProducts() {
  const { user } = useAuth();
  const userId = user?.id;

  return useQuery({
    queryKey: productKeys.list(userId ?? 'anonymous'),
    enabled: !!userId,
    queryFn: async (): Promise<Product[]> => {
      const { data, error } = await supabase
        .from('products')
        .select('*')
        .order('warranty_expires_at', { ascending: true, nullsFirst: false })
        .order('created_at', { ascending: false });

      if (error) throw error;
      return data;
    },
  });
}

export function useProduct(id: string | undefined) {
  return useQuery({
    queryKey: productKeys.detail(id ?? 'none'),
    enabled: !!id,
    queryFn: async (): Promise<Product> => {
      const { data, error } = await supabase
        .from('products')
        .select('*')
        .eq('id', id!)
        .single();

      if (error) throw error;
      return data;
    },
  });
}

export function useCreateProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: ProductInsert): Promise<Product> => {
      const { data, error } = await supabase
        .from('products')
        .insert(input)
        .select()
        .single();

      // Includes the free-tier cap (PT402); callers use isFreeTierLimitError to
      // tell that apart from a genuine failure.
      if (error) throw error;
      return data;
    },
    onSuccess: (product) => {
      // Belt-and-braces delivery: the server cron owns the primary push, and
      // these local notifications still fire if push is unavailable.
      void syncLocalReminders(product).catch(() => {});
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
    },
  });
}

export function useUpdateProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      id,
      changes,
    }: {
      id: string;
      changes: ProductUpdate;
    }): Promise<Product> => {
      const { data, error } = await supabase
        .from('products')
        .update(changes)
        .eq('id', id)
        .select()
        .single();

      if (error) throw error;
      return data;
    },
    onSuccess: (product) => {
      // The expiry may have moved, so the local schedule is rebuilt from scratch.
      void syncLocalReminders(product).catch(() => {});
      queryClient.setQueryData(productKeys.detail(product.id), product);
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
    },
  });
}

export function useDeleteProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (product: Product): Promise<void> => {
      const { error } = await supabase.from('products').delete().eq('id', product.id);
      if (error) throw error;

      // Storage has no foreign key to the row, so the image would otherwise be
      // orphaned and keep counting against the user's storage. A failure here
      // must not surface as a failed delete: the row is already gone.
      if (product.receipt_image_path) {
        await deleteReceiptImage(product.receipt_image_path).catch(() => {});
      }
    },
    onSuccess: (_result, product) => {
      // The database cascade removes the pending rows; this clears the
      // notifications already handed to the OS, which it cannot reach.
      void cancelLocalReminders(product.id).catch(() => {});
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
    },
  });
}
