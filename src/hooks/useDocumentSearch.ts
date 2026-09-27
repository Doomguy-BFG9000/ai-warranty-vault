import { useMutation } from '@tanstack/react-query';

import { aiRequest } from '@/lib/aiApi';
import type { SearchMode, SearchResponse } from '@/types/ai';

export interface SearchInput {
  query: string;
  /** Narrow to one product. Omitted, it searches every document the user owns. */
  productId?: string;
  mode?: SearchMode;
  topK?: number;
}

/**
 * Semantic and hybrid search over the user's own documents.
 *
 * A mutation rather than a query: searches are user-initiated and their results are
 * not something to keep warm in a cache keyed by every keystroke.
 */
export function useDocumentSearch() {
  return useMutation({
    mutationFn: ({ query, productId, mode, topK }: SearchInput) =>
      aiRequest<SearchResponse>('/v1/search', {
        method: 'POST',
        body: {
          query: query.trim(),
          product_id: productId,
          mode,
          top_k: topK,
        },
      }),
  });
}
