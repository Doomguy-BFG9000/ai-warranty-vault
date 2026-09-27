-- ─────────────────────────────────────────────────────────────────────────────
-- Product documents, chunks, embeddings and the indexing queue
--
-- This is the storage layer for the document assistant: a user attaches a
-- manual, warranty card or service terms to one of their products, the file is
-- extracted into page-anchored chunks, each chunk is embedded, and questions are
-- answered only from chunks that were actually retrieved.
--
-- Four ideas shape the schema:
--
--   1. Ownership is denormalised onto every row (user_id on documents, chunks
--      and jobs). Row Level Security can then be a single equality test on an
--      indexed column, which means the filter is pushed *into* the vector scan
--      rather than applied to its output. Filtering after top-k would leak
--      capacity — and eventually rows — across accounts.
--
--   2. A chunk records which embedding space it lives in (embedding_model,
--      embedding_dim, index_version). Vectors from two different models are not
--      comparable, so mixing them silently would quietly ruin retrieval.
--
--   3. Indexing is a durable job, not a background task in a web process. The
--      queue is a table, claimed with FOR UPDATE SKIP LOCKED and a lease, so a
--      worker that is killed mid-document is recovered rather than lost.
--
--   4. Everything derived from a document (chunks, embeddings, jobs) hangs off
--      it by ON DELETE CASCADE, so deleting the document cannot leave a
--      searchable orphan behind.
-- ─────────────────────────────────────────────────────────────────────────────

-- Supabase ships an `extensions` schema; creating it here keeps the migration
-- runnable on a bare Postgres too (the test harness uses one).
create schema if not exists extensions;
create extension if not exists vector with schema extensions;


-- ── enums ───────────────────────────────────────────────────────────────────
create type public.document_kind as enum (
  'manual',          -- user guide / instrukcja obsługi
  'warranty_card',   -- karta gwarancyjna
  'service_terms',   -- warunki serwisu
  'other'
);

-- The lifecycle a document walks through. 'ready' is the only state in which
-- its chunks may be searched; anything else is shown to the user as progress or
-- as a failure they can retry.
create type public.document_index_status as enum (
  'pending',      -- registered, waiting for a worker
  'extracting',   -- pulling text out of the file
  'chunking',
  'embedding',
  'ready',
  'failed'
);

create type public.document_extraction_method as enum (
  'pdf_text',     -- embedded text layer, page numbers are real
  'plain_text',   -- .txt, no pages; chunks cite a character range
  'vision_ocr'    -- scanned PDF or photo, read by a vision model
);

create type public.index_job_state as enum (
  'queued',
  'running',
  'succeeded',
  'failed'
);


-- ── product_documents ───────────────────────────────────────────────────────
create table public.product_documents (
  id                 uuid primary key default gen_random_uuid(),
  product_id         uuid not null references public.products (id) on delete cascade,
  user_id            uuid not null references auth.users (id) on delete cascade,

  title              text not null,
  kind               public.document_kind not null default 'other',

  -- Object path in the private `product-documents` bucket, always
  -- {user_id}/{product_id}/{uuid}.{ext}. The storage policies authorise on the
  -- first path segment, so this shape is load-bearing, not cosmetic.
  storage_path       text not null,
  mime_type          text not null,
  byte_size          bigint not null,

  status             public.document_index_status not null default 'pending',
  extraction_method  public.document_extraction_method,

  -- Number of pages actually read (a scan may be truncated at the page cap);
  -- null for plain text, which has no pages.
  page_count         integer,
  chunk_count        integer not null default 0,

  -- sha256 of the *extracted text*, not the file. Two uploads of the same
  -- scanned page rarely produce byte-identical files but do produce identical
  -- text, and it is the text that the index is built from — so this is the hash
  -- that makes re-running a job a no-op.
  content_sha256     text,

  -- The embedding space and chunking parameters this document was indexed
  -- under. A mismatch against the running configuration means "reindex", never
  -- "search anyway".
  index_version      text,
  embedding_model    text,
  embedding_dim      integer,

  -- Machine-readable reason a document failed, so the app can offer the right
  -- next step (retry vs. "this file has no readable text").
  error_code         text,
  error_message      text,

  indexed_at         timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),

  constraint product_documents_title_not_blank check (length(btrim(title)) > 0),
  constraint product_documents_byte_size_positive check (byte_size > 0),
  constraint product_documents_path_shape
    check (storage_path ~ ('^' || user_id::text || '/' || product_id::text || '/[A-Za-z0-9._-]+$')),
  constraint product_documents_page_count_sane
    check (page_count is null or page_count between 0 and 500)
);

comment on table public.product_documents is
  'A manual, warranty card or service-terms file attached to one product.';
comment on column public.product_documents.content_sha256 is
  'sha256 of the extracted text. Equal hash + equal index_version means the existing chunks are already correct.';
comment on column public.product_documents.index_version is
  'Fingerprint of (chunker params, embedding model, dimensions). Chunks from a different version are never mixed into one search.';

create index product_documents_user_idx on public.product_documents (user_id);
create index product_documents_product_idx on public.product_documents (product_id);
create index product_documents_status_idx on public.product_documents (user_id, status);

-- One row per stored object: re-registering the same upload must not create a
-- second document pointing at the same file.
create unique index product_documents_storage_path_key
  on public.product_documents (storage_path);


-- ── document_chunks ─────────────────────────────────────────────────────────
-- Dimensionality is fixed in the column type because pgvector needs a known
-- width to build an index. 1024 is the configured output width of the
-- embedding model (see services/ai/app/config.py); the service refuses to start
-- if its configuration disagrees with this column, rather than writing vectors
-- that would silently be wrong. Changing providers is a migration, by design.
create table public.document_chunks (
  id              uuid primary key default gen_random_uuid(),
  document_id     uuid not null references public.product_documents (id) on delete cascade,
  product_id      uuid not null references public.products (id) on delete cascade,
  user_id         uuid not null references auth.users (id) on delete cascade,

  -- Position within the document, in reading order. Stable for a given
  -- (document text, index_version) because chunking is deterministic.
  chunk_index     integer not null,

  -- Null for plain text, which genuinely has no pages. Inventing "page 1" for a
  -- .txt file would put a fabricated number in a citation.
  page_number     integer,

  -- Human-readable source label shown in a citation, e.g. 'page 3' or
  -- 'characters 1200–2050'. Rendered by the app, never produced by the model.
  locator         text not null,

  content         text not null,
  content_sha256  text not null,
  token_count     integer not null,

  -- Lexical half of hybrid search. The 'simple' configuration is deliberate: it
  -- lowercases and tokenises without stemming or a stopword list, which is what
  -- makes "E17", "WH-1000XM6" and Polish word forms survive intact. Semantic
  -- matching is the vector half's job, so no stemmer is needed here — and it
  -- means a document's language does not have to be guessed before indexing.
  content_tsv     tsvector generated always as
                    (to_tsvector('simple'::regconfig, content)) stored,

  embedding       extensions.vector(1024),
  embedding_model text not null,
  embedding_dim   integer not null,
  index_version   text not null,

  created_at      timestamptz not null default now(),

  constraint document_chunks_content_not_blank check (length(btrim(content)) > 0),
  constraint document_chunks_index_non_negative check (chunk_index >= 0),
  constraint document_chunks_page_positive check (page_number is null or page_number >= 1),
  constraint document_chunks_dim_matches check (embedding_dim = 1024)
);

comment on table public.document_chunks is
  'Page-anchored slice of a document plus its embedding. The only text the answering model is allowed to use.';
comment on column public.document_chunks.locator is
  'Citation label. Derived from page_number or a character range — never from model output.';

-- Idempotency: re-running an indexing job writes the same (document,
-- index_version, chunk_index) rows instead of duplicating them.
create unique index document_chunks_identity_key
  on public.document_chunks (document_id, index_version, chunk_index);

-- The access filter for every search. Composite so the RLS predicate and the
-- optional product narrowing are both satisfied from one index.
create index document_chunks_user_product_idx
  on public.document_chunks (user_id, product_id);
create index document_chunks_document_idx on public.document_chunks (document_id);

-- Approximate nearest neighbour for growth. Per-user corpora are small enough
-- that the planner usually picks an exact scan, which is the more accurate
-- choice; this index keeps large vaults fast. Cosine matches the normalised
-- vectors the embedding provider returns.
create index document_chunks_embedding_hnsw_idx
  on public.document_chunks
  using hnsw (embedding extensions.vector_cosine_ops);

create index document_chunks_content_tsv_idx
  on public.document_chunks using gin (content_tsv);


-- ── document_index_jobs ─────────────────────────────────────────────────────
-- A Postgres-backed queue. Deliberately not Redis/Celery: the workload is a
-- handful of documents per user, the database is already there, and
-- FOR UPDATE SKIP LOCKED with a lease gives at-least-once delivery with
-- crash recovery in a few lines of SQL.
create table public.document_index_jobs (
  id            uuid primary key default gen_random_uuid(),
  document_id   uuid not null references public.product_documents (id) on delete cascade,
  user_id       uuid not null references auth.users (id) on delete cascade,

  state         public.index_job_state not null default 'queued',
  attempts      integer not null default 0,
  max_attempts  integer not null default 3,

  -- Backoff: a transient failure re-queues the job into the future rather than
  -- spinning on it.
  run_after     timestamptz not null default now(),

  -- Lease. A worker that dies leaves locked_at behind; another worker reclaims
  -- the job once the lease is older than the configured timeout.
  locked_by     text,
  locked_at     timestamptz,

  last_error    text,
  index_version text,

  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),

  constraint document_index_jobs_attempts_sane check (attempts >= 0 and max_attempts > 0)
);

comment on table public.document_index_jobs is
  'Durable indexing queue. Claimed with FOR UPDATE SKIP LOCKED; stale leases are reclaimable.';

-- At most one live job per document, so a double-tap on "retry" cannot start
-- two indexing runs over the same file.
create unique index document_index_jobs_one_active_key
  on public.document_index_jobs (document_id)
  where state in ('queued', 'running');

create index document_index_jobs_claim_idx
  on public.document_index_jobs (state, run_after);
create index document_index_jobs_user_idx on public.document_index_jobs (user_id);


-- ── ai_threads / ai_messages ────────────────────────────────────────────────
-- A conversation is scoped to one user and (optionally) one product: the graph
-- never reasons across products the question was not about.
create table public.ai_threads (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  product_id uuid references public.products (id) on delete cascade,
  title      text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.ai_threads is
  'A question-and-answer conversation. Its id is also the LangGraph thread_id; ownership is checked here on every resume.';

create index ai_threads_user_idx on public.ai_threads (user_id, updated_at desc);

create type public.ai_message_role as enum ('user', 'assistant');

-- Why answers are mirrored here rather than read back out of the checkpointer:
-- checkpoints are the graph's private working state (tool calls, scratch,
-- serialised messages) and their shape belongs to LangGraph. The transcript the
-- app renders is a product surface, so it gets its own table with its own
-- citation contract — and reopening the app restores the conversation without
-- deserialising graph internals.
create table public.ai_messages (
  id         uuid primary key default gen_random_uuid(),
  thread_id  uuid not null references public.ai_threads (id) on delete cascade,
  user_id    uuid not null references auth.users (id) on delete cascade,
  role       public.ai_message_role not null,
  content    text not null,

  -- Validated citations: [{chunk_id, document_id, document_title, page_number,
  -- locator, quote}]. The server rewrites this from the retrieved context, so a
  -- chunk_id here always exists and always belongs to this user.
  citations  jsonb not null default '[]'::jsonb,

  -- 'answered' | 'no_answer' | 'conflicting_sources' | 'clarification_needed' |
  -- 'draft_ready' | 'error'. Lets the app render abstention as a first-class
  -- state instead of styling a sentence.
  outcome    text,

  -- Non-secret run metadata (model, prompt version, latency, token usage where
  -- the provider reports it). No document text, no tokens, no keys.
  meta       jsonb not null default '{}'::jsonb,

  created_at timestamptz not null default now(),

  constraint ai_messages_content_not_blank check (length(btrim(content)) > 0),
  constraint ai_messages_citations_is_array check (jsonb_typeof(citations) = 'array')
);

create index ai_messages_thread_idx on public.ai_messages (thread_id, created_at);


-- ── claim_drafts ────────────────────────────────────────────────────────────
-- The human-in-the-loop artefact. The graph proposes a version; a person edits
-- and approves one specific version; approval is recorded exactly once and the
-- approved text is frozen in its own column so a later model run cannot rewrite
-- what was signed off.
create type public.claim_draft_status as enum (
  'pending',     -- proposed, waiting for the user
  'approved',
  'rejected',
  'superseded'   -- a newer version was proposed before this one was decided
);

create table public.claim_drafts (
  id             uuid primary key default gen_random_uuid(),
  thread_id      uuid not null references public.ai_threads (id) on delete cascade,
  user_id        uuid not null references auth.users (id) on delete cascade,
  product_id     uuid not null references public.products (id) on delete cascade,

  version        integer not null default 1,
  status         public.claim_draft_status not null default 'pending',

  subject        text not null,
  body           text not null,

  -- The structured facts the draft was built from (product fields, computed
  -- warranty dates) and the chunk citations backing any quoted condition. Both
  -- server-produced.
  facts          jsonb not null default '{}'::jsonb,
  citations      jsonb not null default '[]'::jsonb,

  -- Hash of the proposed (subject, body). Doubles as the idempotency key for a
  -- replayed drafting node: identical hash means "this draft already exists".
  content_sha256 text not null,

  -- What the user actually approved, captured at approval time. Kept separate
  -- from body so that "what the model wrote" and "what the human signed" can
  -- never be confused, even if they are usually identical.
  approved_subject text,
  approved_body    text,
  approved_at      timestamptz,

  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),

  constraint claim_drafts_subject_not_blank check (length(btrim(subject)) > 0),
  constraint claim_drafts_body_not_blank check (length(btrim(body)) > 0),
  constraint claim_drafts_version_positive check (version >= 1),
  -- An approved row must carry the approved text and timestamp, and a
  -- non-approved row must not. Makes "approved" unambiguous at the schema level.
  constraint claim_drafts_approval_complete check (
    (status = 'approved') = (approved_at is not null
                             and approved_subject is not null
                             and approved_body is not null)
  )
);

comment on table public.claim_drafts is
  'A service-claim draft awaiting human approval. Never sent anywhere — approval only persists the text.';

create unique index claim_drafts_version_key
  on public.claim_drafts (thread_id, version);

-- Exactly-once approval, enforced by the database rather than by application
-- care: a second approval in the same thread cannot be written at all.
create unique index claim_drafts_single_approved_key
  on public.claim_drafts (thread_id)
  where status = 'approved';

-- Replaying the drafting node must find the draft it already wrote instead of
-- proposing a near-identical second one.
create unique index claim_drafts_content_key
  on public.claim_drafts (thread_id, content_sha256);

create index claim_drafts_user_idx on public.claim_drafts (user_id, created_at desc);


-- ── ai_operation_log ────────────────────────────────────────────────────────
-- Observability for AI calls. Deliberately records *shapes*, not content: an
-- operation id to correlate stages, which stage, how long, which model and
-- prompt version, provider-reported token counts, and whether it failed. No
-- document text, no prompts, no user tokens, no keys — this table is meant to
-- be safe to read while debugging someone else's problem.
create table public.ai_operation_log (
  id             bigserial primary key,
  operation_id   uuid not null,
  user_id        uuid references auth.users (id) on delete set null,

  stage          text not null,
  status         text not null,
  duration_ms    integer,

  model          text,
  prompt_version text,
  input_tokens   integer,
  output_tokens  integer,

  -- Bounded, non-sensitive counters (chunk counts, top-k, page counts).
  detail         jsonb not null default '{}'::jsonb,
  error_code     text,

  created_at     timestamptz not null default now(),

  constraint ai_operation_log_status_known
    check (status in ('ok', 'error', 'skipped'))
);

comment on table public.ai_operation_log is
  'Per-stage AI telemetry. Never contains document text, prompts, tokens or keys.';

create index ai_operation_log_operation_idx on public.ai_operation_log (operation_id);
create index ai_operation_log_created_idx on public.ai_operation_log (created_at desc);


-- ── updated_at maintenance ──────────────────────────────────────────────────
create trigger product_documents_set_updated_at
  before update on public.product_documents
  for each row execute function public.set_updated_at();

create trigger document_index_jobs_set_updated_at
  before update on public.document_index_jobs
  for each row execute function public.set_updated_at();

create trigger ai_threads_set_updated_at
  before update on public.ai_threads
  for each row execute function public.set_updated_at();

create trigger claim_drafts_set_updated_at
  before update on public.claim_drafts
  for each row execute function public.set_updated_at();
