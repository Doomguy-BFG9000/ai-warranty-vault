/**
 * Contract with the document-assistant service (`services/ai`).
 *
 * Mirrors `services/ai/app/schemas.py`. Kept as a hand-written mirror rather than a
 * generated client so the app depends on a small, readable surface — and so a change
 * to the service that the app has not been taught about shows up as a type error.
 */

export type DocumentKind = 'manual' | 'warranty_card' | 'service_terms' | 'other';

/** Lifecycle of a document's index. Only `ready` documents are searchable. */
export type DocumentStatus =
  | 'pending'
  | 'extracting'
  | 'chunking'
  | 'embedding'
  | 'ready'
  | 'failed';

export type SearchMode = 'vector' | 'lexical' | 'hybrid';

export interface ProductDocument {
  id: string;
  product_id: string;
  title: string;
  kind: DocumentKind;
  status: DocumentStatus;
  mime_type: string;
  byte_size: number;
  page_count: number | null;
  chunk_count: number;
  extraction_method: 'pdf_text' | 'plain_text' | 'vision_ocr' | null;
  index_version: string | null;
  embedding_model: string | null;
  error_code: string | null;
  /** A failure reason, or a non-fatal note such as "only the first 12 pages were read". */
  notice: string | null;
  indexed_at: string | null;
  created_at: string;
  /** Set while an indexing job is live, which is how "failed" and "retrying" differ. */
  job_state: 'queued' | 'running' | 'succeeded' | 'failed' | null;
  job_attempts: number | null;
  /** Indexed under an older embedding space, so its chunks are excluded from search. */
  stale_index: boolean;
}

export interface DocumentChunk {
  chunk_id: string;
  document_id: string;
  chunk_index: number;
  page_number: number | null;
  /** Human-readable source label: "page 3", or a character range for plain text. */
  locator: string;
  content: string;
  token_count: number;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_title: string | null;
  product_id: string;
  page_number: number | null;
  locator: string;
  snippet: string;
  score: number;
  vector_similarity: number | null;
  lexical_rank: number | null;
  vector_position: number | null;
  lexical_position: number | null;
}

export interface SearchResponse {
  mode: SearchMode;
  top_k: number;
  index_version: string;
  hits: SearchHit[];
  /** True when there is nothing indexed to search — a different problem from "no match". */
  no_documents: boolean;
}

/** A validated citation. Every field is produced by the server from its own records. */
export interface Citation {
  label: string;
  chunk_id: string;
  document_id: string;
  document_title: string | null;
  page_number: number | null;
  locator: string;
}

/**
 * How a turn ended. `no_answer` is a first-class result, not an error: it means the
 * documents do not support an answer, and the UI should say exactly that.
 */
export type TurnOutcome =
  | 'answered'
  | 'no_answer'
  | 'conflicting_sources'
  | 'clarification_needed'
  | 'draft_ready'
  | 'draft_approved'
  | 'draft_rejected'
  | 'error'
  | 'idle';

export interface AssistantMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations: Citation[];
  outcome: TurnOutcome | null;
  /**
   * True when the answer rests on the user's saved product record and the warranty
   * dates computed from it, rather than on a document passage. Reported separately
   * from citations: a computed date must never be shown as though a document had
   * stated it.
   */
  used_product_facts: boolean;
  created_at: string;
}

export interface ClaimDraft {
  draft_id: string;
  version: number;
  status: 'pending' | 'approved' | 'rejected' | 'superseded';
  subject: string;
  body: string;
  citations: Citation[];
  /** What the user still needs to supply; the model is told to name gaps, not fill them. */
  missing_information: string[];
  approved_subject: string | null;
  approved_body: string | null;
  approved_at: string | null;
}

/** What the conversation is waiting for. No graph internals cross this boundary. */
export interface AwaitingInput {
  kind: 'clarification' | 'claim_draft';
  question: string | null;
  draft: ClaimDraft | null;
}

export interface ThreadState {
  thread_id: string;
  product_id: string | null;
  title: string | null;
  messages: AssistantMessage[];
  awaiting: AwaitingInput | null;
  latest_draft: ClaimDraft | null;
  updated_at: string;
}

export interface TurnResponse {
  thread_id: string;
  outcome: TurnOutcome;
  message: AssistantMessage | null;
  awaiting: AwaitingInput | null;
  draft_result: { applied: boolean; reason?: string } | null;
}

export interface AiServiceMeta {
  provider_mode: 'live' | 'mock';
  answer_model: string;
  embedding_model: string | null;
  index_version: string;
  retrieval: { mode: SearchMode; top_k: number };
  limits: { max_document_bytes: number; max_document_pages: number; max_ocr_pages: number };
}
