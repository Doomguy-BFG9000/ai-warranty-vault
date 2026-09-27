-- ─────────────────────────────────────────────────────────────────────────────
-- Row Level Security for the document assistant
--
-- The FastAPI service does not get a privileged shortcut. For request-scoped
-- work it connects, then runs
--
--     set local role authenticated;
--     set local request.jwt.claims = '{"sub":"<verified uid>", ...}';
--
-- which is exactly what PostgREST does — so the policies below are the same
-- boundary for the API as they are for the mobile client talking to PostgREST
-- directly. There is no code path that reads a user_id out of a request body.
--
-- Two deliberate asymmetries:
--
--   * Chunks and embeddings are SELECT-only for `authenticated`. They are
--     derived data; only the indexing worker (service_role) may write them, so
--     a client cannot inject text into its own retrieval context.
--   * Claim drafts are insert/update-able by their owner, because approving a
--     draft is a user action. The *shape* of a legal approval is pinned by the
--     claim_drafts_approval_complete constraint and the single-approved unique
--     index, so "approve" cannot be twisted into "rewrite someone's approved
--     text" or "approve twice".
-- ─────────────────────────────────────────────────────────────────────────────

-- ── privileges ──────────────────────────────────────────────────────────────
-- RLS narrows access, it never grants it. Without these the API gets
-- "permission denied for table ..." regardless of the policies.

grant select, insert, update, delete on public.product_documents  to authenticated;
grant select                         on public.document_chunks     to authenticated;
grant select, insert                 on public.document_index_jobs to authenticated;
grant select, insert, update, delete on public.ai_threads          to authenticated;
grant select, insert                 on public.ai_messages         to authenticated;
-- Drafts are *proposed* by the server and *decided* by the user, so there is no
-- client INSERT: only select, plus the update that records a decision.
grant select, update                 on public.claim_drafts        to authenticated;

-- The log is written by the service and is not a client-facing surface. Users
-- get no access at all: even without content it is an operational trace.
grant select, insert, update, delete
  on public.product_documents, public.document_chunks, public.document_index_jobs,
     public.ai_threads, public.ai_messages, public.claim_drafts, public.ai_operation_log
  to service_role;

grant usage, select on sequence public.ai_operation_log_id_seq to service_role;


-- ── enable RLS ──────────────────────────────────────────────────────────────
alter table public.product_documents   enable row level security;
alter table public.document_chunks     enable row level security;
alter table public.document_index_jobs enable row level security;
alter table public.ai_threads          enable row level security;
alter table public.ai_messages         enable row level security;
alter table public.claim_drafts        enable row level security;
alter table public.ai_operation_log    enable row level security;


-- ── product_documents ───────────────────────────────────────────────────────
-- The WITH CHECK on insert does three things at once: the row must be owned by
-- the caller, and the product it points at must also be theirs. The product test
-- is an EXISTS against products, which is itself under RLS — so passing a
-- stranger's product_id fails the same way a made-up one does, and the error
-- says nothing about whether that product exists.
create policy "Users can view own documents"
  on public.product_documents for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users can add documents to own products"
  on public.product_documents for insert
  to authenticated
  with check (
    (select auth.uid()) = user_id
    and exists (
      select 1 from public.products p
      where p.id = product_id and p.user_id = (select auth.uid())
    )
  );

-- Retitling and re-kinding a document is a user action; the columns that decide
-- what is searchable (status, chunk counts, embedding space) are protected by
-- the trigger further down.
create policy "Users can update own documents"
  on public.product_documents for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "Users can delete own documents"
  on public.product_documents for delete
  to authenticated
  using ((select auth.uid()) = user_id);


-- ── document_chunks ─────────────────────────────────────────────────────────
-- Read-only, and read-only on an indexed equality so the predicate is pushed
-- into the vector scan rather than applied to its result. This is the line that
-- makes "filter before top-k" true.
create policy "Users can read own chunks"
  on public.document_chunks for select
  to authenticated
  using ((select auth.uid()) = user_id);


-- ── document_index_jobs ─────────────────────────────────────────────────────
-- A user may queue a retry for their own document. Claiming, leasing and
-- failing jobs is the worker's business, so there is no client UPDATE policy.
create policy "Users can view own index jobs"
  on public.document_index_jobs for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users can queue jobs for own documents"
  on public.document_index_jobs for insert
  to authenticated
  with check (
    (select auth.uid()) = user_id
    and exists (
      select 1 from public.product_documents d
      where d.id = document_id and d.user_id = (select auth.uid())
    )
  );


-- ── ai_threads ──────────────────────────────────────────────────────────────
create policy "Users can view own threads"
  on public.ai_threads for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users can create own threads"
  on public.ai_threads for insert
  to authenticated
  with check (
    (select auth.uid()) = user_id
    and (
      product_id is null
      or exists (
        select 1 from public.products p
        where p.id = product_id and p.user_id = (select auth.uid())
      )
    )
  );

create policy "Users can update own threads"
  on public.ai_threads for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "Users can delete own threads"
  on public.ai_threads for delete
  to authenticated
  using ((select auth.uid()) = user_id);


-- ── ai_messages ─────────────────────────────────────────────────────────────
-- Users may append their own turns. Assistant turns carry citations that the
-- server validated, so they are written by service_role only — the trigger
-- below rejects a client trying to author one.
create policy "Users can read own messages"
  on public.ai_messages for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users can append to own threads"
  on public.ai_messages for insert
  to authenticated
  with check (
    (select auth.uid()) = user_id
    and exists (
      select 1 from public.ai_threads t
      where t.id = thread_id and t.user_id = (select auth.uid())
    )
  );


-- ── claim_drafts ────────────────────────────────────────────────────────────
create policy "Users can read own drafts"
  on public.claim_drafts for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users can decide own drafts"
  on public.claim_drafts for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);


-- ── ai_operation_log ────────────────────────────────────────────────────────
-- RLS is enabled with no policy at all, which denies `authenticated` outright.
-- service_role bypasses RLS, so the service can still write.
comment on table public.ai_operation_log is
  'Per-stage AI telemetry. RLS enabled with no policy: clients have no access. Never contains document text, prompts, tokens or keys.';


-- ── protect server-owned columns on product_documents ───────────────────────
-- The update policy above exists so a user can rename a document. It must not
-- become a way to declare a document 'ready', claim a different embedding space,
-- or point the row at another object in storage — any of which would put
-- unverified text into somebody's retrieval context.
--
-- SECURITY INVOKER on purpose: the guard keys off current_user, which PostgREST
-- sets to 'authenticated' for app traffic while the worker connects as itself.
-- A definer function would report its owner instead and lock out the worker.
create or replace function public.guard_document_server_columns()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if current_user in ('authenticated', 'anon') then
    if new.status            is distinct from old.status
       or new.storage_path   is distinct from old.storage_path
       or new.mime_type      is distinct from old.mime_type
       or new.byte_size      is distinct from old.byte_size
       or new.product_id     is distinct from old.product_id
       or new.user_id        is distinct from old.user_id
       or new.content_sha256 is distinct from old.content_sha256
       or new.index_version  is distinct from old.index_version
       or new.embedding_model is distinct from old.embedding_model
       or new.embedding_dim  is distinct from old.embedding_dim
       or new.chunk_count    is distinct from old.chunk_count
       or new.page_count     is distinct from old.page_count
       or new.extraction_method is distinct from old.extraction_method
       or new.indexed_at     is distinct from old.indexed_at
    then
      raise exception
        'FORBIDDEN: indexing state is maintained by the indexing worker, not the client.'
        using errcode = 'PT403';
    end if;
  end if;
  return new;
end;
$$;

create trigger product_documents_guard_server_columns
  before update on public.product_documents
  for each row execute function public.guard_document_server_columns();


-- ── protect the approval record on claim_drafts ─────────────────────────────
-- The user's legitimate move is: pending -> approved (with the text they
-- signed), or pending -> rejected. Everything else — re-approving, editing an
-- approved draft, back-dating an approval, changing the citations the draft
-- claims to rest on — is rejected here. The single-approved unique index is the
-- second lock: even a racing pair of approvals can only land one row.
create or replace function public.guard_claim_draft_transition()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if current_user not in ('authenticated', 'anon') then
    return new;
  end if;

  if old.status <> 'pending' then
    raise exception
      'FORBIDDEN: this draft has already been decided and cannot be changed.'
      using errcode = 'PT409';
  end if;

  if new.status not in ('pending', 'approved', 'rejected') then
    raise exception 'FORBIDDEN: unsupported draft status change.'
      using errcode = 'PT403';
  end if;

  -- The model's proposal, the facts and the citations are the server's record of
  -- what was offered. The user approves or rejects it; they do not get to
  -- rewrite the offer. Their own edits live in approved_subject/approved_body.
  if new.subject is distinct from old.subject
     or new.body is distinct from old.body
     or new.facts is distinct from old.facts
     or new.citations is distinct from old.citations
     or new.content_sha256 is distinct from old.content_sha256
     or new.version is distinct from old.version
     or new.thread_id is distinct from old.thread_id
     or new.product_id is distinct from old.product_id
     or new.user_id is distinct from old.user_id
  then
    raise exception
      'FORBIDDEN: the proposed draft is immutable; approve, reject, or ask for a new version.'
      using errcode = 'PT403';
  end if;

  if new.status = 'approved' then
    -- now(), not a client-supplied timestamp.
    new.approved_at := now();
    if new.approved_subject is null or new.approved_body is null then
      raise exception 'FORBIDDEN: an approval must record the text being approved.'
        using errcode = 'PT422';
    end if;
  end if;

  if new.status = 'rejected' then
    new.approved_at := null;
    new.approved_subject := null;
    new.approved_body := null;
  end if;

  return new;
end;
$$;

create trigger claim_drafts_guard_transition
  before update on public.claim_drafts
  for each row execute function public.guard_claim_draft_transition();


-- ── assistant turns are server-authored ─────────────────────────────────────
-- Citations are the contract that an answer is grounded. If a client could write
-- an assistant message it could fabricate that contract, so the role is pinned.
create or replace function public.guard_assistant_messages()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if current_user in ('authenticated', 'anon') and new.role <> 'user' then
    raise exception
      'FORBIDDEN: assistant messages are written by the server, not the client.'
      using errcode = 'PT403';
  end if;
  return new;
end;
$$;

create trigger ai_messages_guard_role
  before insert on public.ai_messages
  for each row execute function public.guard_assistant_messages();
