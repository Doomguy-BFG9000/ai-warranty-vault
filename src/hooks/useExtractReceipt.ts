import { useMutation } from '@tanstack/react-query';

import { supabase } from '@/lib/supabase';
import type { ExtractionResponse, ReceiptExtraction } from '@/types/extraction';

/**
 * Runs a stored receipt through the extract-receipt Edge Function.
 *
 * The function is invoked with the user's session, so Storage RLS — not this
 * client — decides which receipt may be read. Failures are non-fatal by design:
 * the review screen falls back to an empty form the user fills in by hand.
 */
export function useExtractReceipt() {
  return useMutation({
    mutationFn: async (receiptPath: string): Promise<ReceiptExtraction> => {
      const { data, error } = await supabase.functions.invoke<ExtractionResponse>(
        'extract-receipt',
        { body: { receiptPath } },
      );

      if (error) throw error;
      if (!data?.extraction) throw new Error('Extraction returned no data.');

      return data.extraction;
    },
    // A failed read should not cost the user their capture; they can still type
    // the details in. Retrying once covers a cold function start.
    retry: 1,
  });
}
