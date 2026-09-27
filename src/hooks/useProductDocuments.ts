import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { useAuth } from '@/hooks/useAuth';
import { aiRequest } from '@/lib/aiApi';
import { pickDocument, uploadProductDocument, type PickedDocument } from '@/lib/documents';
import { env } from '@/lib/env';
import type { DocumentChunk, DocumentKind, ProductDocument } from '@/types/ai';

export const documentKeys = {
  all: ['product-documents'] as const,
  list: (productId: string) => [...documentKeys.all, 'list', productId] as const,
  detail: (documentId: string) => [...documentKeys.all, 'detail', documentId] as const,
  chunks: (documentId: string) => [...documentKeys.all, 'chunks', documentId] as const,
};

/** True while a document is on its way to being searchable. */
export function isIndexing(document: ProductDocument): boolean {
  return (
    document.status === 'pending' ||
    document.status === 'extracting' ||
    document.status === 'chunking' ||
    document.status === 'embedding' ||
    document.job_state === 'queued' ||
    document.job_state === 'running'
  );
}

/** A short phrase for the current state, in the user's terms rather than the schema's. */
export function describeStatus(document: ProductDocument): string {
  if (document.job_state === 'queued' && document.status === 'failed') return 'Retrying…';
  switch (document.status) {
    case 'pending':
      return 'Queued';
    case 'extracting':
      return 'Reading the file…';
    case 'chunking':
      return 'Splitting into passages…';
    case 'embedding':
      return 'Indexing…';
    case 'ready':
      return document.stale_index
        ? 'Needs reindexing'
        : `Ready · ${document.chunk_count} passage${document.chunk_count === 1 ? '' : 's'}`;
    case 'failed':
      return 'Could not be read';
  }
}

export function useProductDocuments(productId: string | undefined) {
  return useQuery({
    queryKey: documentKeys.list(productId ?? 'none'),
    enabled: !!productId && env.hasAiApi,
    queryFn: () => aiRequest<ProductDocument[]>(`/v1/products/${productId}/documents`),
    /**
     * Indexing is asynchronous, so the list polls while anything is in flight and
     * stops as soon as everything has settled. Cheaper and far simpler than a
     * realtime subscription for a list of half a dozen rows.
     */
    refetchInterval: (query) => {
      const documents = query.state.data;
      if (!documents) return false;
      return documents.some(isIndexing) ? 2_000 : false;
    },
  });
}

export function useProductDocument(documentId: string | undefined) {
  return useQuery({
    queryKey: documentKeys.detail(documentId ?? 'none'),
    enabled: !!documentId && env.hasAiApi,
    queryFn: () => aiRequest<ProductDocument>(`/v1/documents/${documentId}`),
    refetchInterval: (query) => (query.state.data && isIndexing(query.state.data) ? 2_000 : false),
  });
}

/** The passages a citation points at — this is what "open the source" opens. */
export function useDocumentChunks(documentId: string | undefined, page?: number | null) {
  return useQuery({
    queryKey: [...documentKeys.chunks(documentId ?? 'none'), page ?? 'all'],
    enabled: !!documentId && env.hasAiApi,
    queryFn: () =>
      aiRequest<DocumentChunk[]>(`/v1/documents/${documentId}/chunks`, {
        query: { page: page ?? undefined, limit: 200 },
      }),
  });
}

export interface AddDocumentInput {
  productId: string;
  title: string;
  kind: DocumentKind;
  /** Omit to open the system picker. */
  picked?: PickedDocument;
}

/**
 * Upload, then register. Two steps because the upload goes straight to Storage and
 * the registration is what starts indexing.
 *
 * If registration fails the uploaded object is removed again, so a rejected file does
 * not sit in the bucket counting against the user's storage with no row to explain it.
 */
export function useAddProductDocument() {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ productId, title, kind, picked }: AddDocumentInput) => {
      if (!user) throw new Error('You need to be signed in.');

      const document = picked ?? (await pickDocument());
      if (!document) return null;

      const { storagePath, byteSize } = await uploadProductDocument(
        user.id,
        productId,
        document,
      );

      try {
        return await aiRequest<ProductDocument>(`/v1/products/${productId}/documents`, {
          method: 'POST',
          body: {
            storage_path: storagePath,
            title: title.trim() || document.name,
            kind,
            mime_type: document.mimeType,
            byte_size: byteSize,
          },
        });
      } catch (error) {
        const { supabase } = await import('@/lib/supabase');
        const { DOCUMENTS_BUCKET } = await import('@/lib/documents');
        await supabase.storage.from(DOCUMENTS_BUCKET).remove([storagePath]).catch(() => {});
        throw error;
      }
    },
    onSuccess: (document) => {
      if (document) {
        void queryClient.invalidateQueries({ queryKey: documentKeys.list(document.product_id) });
      }
    },
  });
}

export function useReindexDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (documentId: string) =>
      aiRequest<ProductDocument>(`/v1/documents/${documentId}/reindex`, { method: 'POST' }),
    onSuccess: (document) => {
      void queryClient.invalidateQueries({ queryKey: documentKeys.list(document.product_id) });
      void queryClient.invalidateQueries({ queryKey: documentKeys.detail(document.id) });
    },
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (document: ProductDocument) => {
      // The service deletes the row (chunks and embeddings cascade) and the stored
      // object, so there is nothing for the client to clean up afterwards.
      await aiRequest<void>(`/v1/documents/${document.id}`, { method: 'DELETE' });
      return document;
    },
    onSuccess: (document) => {
      void queryClient.invalidateQueries({ queryKey: documentKeys.list(document.product_id) });
      queryClient.removeQueries({ queryKey: documentKeys.detail(document.id) });
    },
  });
}
