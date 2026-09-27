import { describeStatus, isIndexing } from '@/hooks/useProductDocuments';
import type { ProductDocument } from '@/types/ai';

// The status helpers are pure, but they live next to hooks that import the Supabase
// client, whose AsyncStorage dependency touches a native module at import time.
// (babel-jest hoists jest.mock above the imports, so the position here is cosmetic.)
jest.mock('@/lib/supabase', () => ({
  supabase: { auth: { getSession: jest.fn() }, storage: { from: jest.fn() } },
}));
jest.mock('expo-document-picker', () => ({ getDocumentAsync: jest.fn() }));

/**
 * Indexing state is most of what the documents screen conveys, and the distinctions
 * matter to a user: "failed" and "failed, retrying" need different buttons, and a
 * document indexed under an older embedding model is not searchable however green
 * "ready" would look.
 */
function document(overrides: Partial<ProductDocument> = {}): ProductDocument {
  return {
    id: 'doc-1',
    product_id: 'product-1',
    title: 'Instrukcja obsługi',
    kind: 'manual',
    status: 'ready',
    mime_type: 'application/pdf',
    byte_size: 12345,
    page_count: 12,
    chunk_count: 37,
    extraction_method: 'pdf_text',
    index_version: 'v1-abcdef12+tiktoken/cl100k_base',
    embedding_model: 'openai/text-embedding-3-large@1024',
    error_code: null,
    notice: null,
    indexed_at: '2026-09-01T10:00:00Z',
    created_at: '2026-09-01T09:59:00Z',
    job_state: null,
    job_attempts: null,
    stale_index: false,
    ...overrides,
  };
}

describe('isIndexing', () => {
  it.each(['pending', 'extracting', 'chunking', 'embedding'] as const)(
    'treats %s as still working',
    (status) => {
      expect(isIndexing(document({ status }))).toBe(true);
    },
  );

  it('is false once a document is ready', () => {
    expect(isIndexing(document())).toBe(false);
  });

  it('is true for a failed document with a live retry job', () => {
    // The row still reads "failed" until the new run reaches it, so the job state is
    // what tells the list to keep polling.
    expect(isIndexing(document({ status: 'failed', job_state: 'queued' }))).toBe(true);
  });

  it('is false for a failed document with no live job', () => {
    expect(isIndexing(document({ status: 'failed', job_state: 'failed' }))).toBe(false);
  });
});

describe('describeStatus', () => {
  it('counts the passages a ready document contributes', () => {
    expect(describeStatus(document({ chunk_count: 37 }))).toBe('Ready · 37 passages');
    expect(describeStatus(document({ chunk_count: 1 }))).toBe('Ready · 1 passage');
  });

  it('says a document needs reindexing rather than calling it ready', () => {
    // Its chunks are excluded from search, so "ready" would be a lie.
    expect(describeStatus(document({ stale_index: true }))).toBe('Needs reindexing');
  });

  it('distinguishes a failure from a retry in progress', () => {
    expect(describeStatus(document({ status: 'failed', job_state: 'failed' }))).toBe(
      'Could not be read',
    );
    expect(describeStatus(document({ status: 'failed', job_state: 'queued' }))).toBe('Retrying…');
  });

  it('names the stage while indexing, so progress is visible', () => {
    expect(describeStatus(document({ status: 'extracting' }))).toBe('Reading the file…');
    expect(describeStatus(document({ status: 'embedding' }))).toBe('Indexing…');
  });
});
