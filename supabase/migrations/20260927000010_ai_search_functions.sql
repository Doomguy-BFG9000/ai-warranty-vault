-- ─────────────────────────────────────────────────────────────────────────────
-- Retrieval, the deterministic warranty helper, and the graph's private schema
--
-- Search lives in SQL rather than in Python for one reason: it must run *inside*
-- the caller's Row Level Security context, so the ownership predicate is part of
-- the scan that produces the top-k rather than a filter applied to its output.
-- The function is SECURITY INVOKER, which is the whole point — a definer
-- function would run as its owner and see everybody's chunks.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── hybrid chunk search ─────────────────────────────────────────────────────
-- Three modes, so the retrieval strategy can be compared honestly on the same
-- corpus and the same questions (see services/ai/eval):
--
--   'vector'  — cosine nearest neighbour only.
--   'lexical' — full-text plus literal code/part-number matching only.
--   'hybrid'  — both candidate lists fused with Reciprocal Rank Fusion.
--
-- RRF is used instead of a weighted sum of scores because a cosine similarity
-- and a ts_rank_cd are not on a common scale: mixing them numerically requires
-- a normalisation that has to be retuned whenever either side changes. RRF only
-- consumes *ranks*, so it needs no such calibration. k = 60 is the constant from
-- the original RRF paper and is not tuned here; changing it re-orders ties, not
-- the head of the list.
create or replace function public.search_document_chunks(
  p_query_embedding extensions.vector(1024),
  p_query_text      text                  default null,
  p_product_id      uuid                  default null,
  p_document_id     uuid                  default null,
  p_match_count     integer               default 8,
  p_mode            text                  default 'hybrid',
  p_candidate_count integer               default 40,
  p_index_version   text                  default null
)
returns table (
  chunk_id           uuid,
  document_id        uuid,
  product_id         uuid,
  page_number        integer,
  locator            text,
  content            text,
  token_count        integer,
  vector_similarity  double precision,
  lexical_rank       double precision,
  vector_position    integer,
  lexical_position   integer,
  score              double precision
)
language sql
stable
security invoker
set search_path = public, extensions, pg_temp
as $$
with params as (
  select
    greatest(1, least(coalesce(p_match_count, 8), 50))          as match_count,
    greatest(1, least(coalesce(p_candidate_count, 40), 200))     as candidate_count,
    lower(coalesce(p_mode, 'hybrid'))                            as mode,
    60.0                                                         as rrf_k
),
-- Every candidate list starts from this view, so the access filter cannot be
-- forgotten on one branch. RLS already restricts the table to the caller; the
-- explicit user_id test is redundancy, and it also gives the planner the
-- selective predicate up front.
visible as (
  select c.*
  from public.document_chunks c
  where c.user_id = (select auth.uid())
    and (p_product_id  is null or c.product_id  = p_product_id)
    and (p_document_id is null or c.document_id = p_document_id)
    and (p_index_version is null or c.index_version = p_index_version)
),
-- Code-ish tokens: error codes (E17, F-05), model numbers (WH-1000XM6), part
-- numbers. These are exactly the strings an embedding model blurs together, and
-- exactly the strings a user types verbatim.
code_tokens as (
  select distinct lower(m[1]) as tok
  from regexp_matches(
         coalesce(p_query_text, ''),
         '([A-Za-z]{0,5}-?[0-9]{1,6}[A-Za-z0-9]{0,10}(?:-[A-Za-z0-9]+)*)',
         'g'
       ) as m
  where length(m[1]) >= 2
),
lex_query as (
  select websearch_to_tsquery('simple', coalesce(p_query_text, '')) as tsq
),
vector_raw as (
  select v.id, v.embedding <=> p_query_embedding as distance
  from visible v, params
  where p_query_embedding is not null
    and v.embedding is not null
    and params.mode in ('vector', 'hybrid')
  order by v.embedding <=> p_query_embedding
  -- LIMIT before ranking: this is what lets the ANN index stop early instead of
  -- scoring the whole table.
  limit (select candidate_count from params)
),
vector_hits as (
  select id,
         1.0 - distance                                          as similarity,
         row_number() over (order by distance)::integer           as position
  from vector_raw
),
lexical_raw as (
  select v.id,
         greatest(
           case when v.content_tsv @@ lq.tsq
                then ts_rank_cd(v.content_tsv, lq.tsq)::double precision
                else 0 end,
           -- A literal code match is worth more than a weak word overlap: it is
           -- the signal the vector side is least able to provide.
           case when exists (
                  select 1 from code_tokens ct
                  where v.content ~* ('\y' || ct.tok || '\y')
                ) then 0.5 else 0 end
         ) as rank
  from visible v, lex_query lq, params
  where params.mode in ('lexical', 'hybrid')
    and (
      v.content_tsv @@ lq.tsq
      or exists (
        select 1 from code_tokens ct
        where v.content ~* ('\y' || ct.tok || '\y')
      )
    )
  order by rank desc, v.chunk_index
  limit (select candidate_count from params)
),
lexical_hits as (
  select id,
         rank,
         row_number() over (order by rank desc, id)::integer as position
  from lexical_raw
),
fused as (
  select
    coalesce(vh.id, lh.id)                                       as id,
    vh.similarity,
    lh.rank,
    vh.position                                                  as vector_position,
    lh.position                                                  as lexical_position,
    coalesce(1.0 / ((select rrf_k from params) + vh.position), 0)
      + coalesce(1.0 / ((select rrf_k from params) + lh.position), 0) as rrf_score
  from vector_hits vh
  full outer join lexical_hits lh on lh.id = vh.id
)
select
  v.id,
  v.document_id,
  v.product_id,
  v.page_number,
  v.locator,
  v.content,
  v.token_count,
  f.similarity,
  f.rank,
  f.vector_position,
  f.lexical_position,
  -- In single-strategy modes the caller gets that strategy's own score, so a
  -- relevance threshold means the same thing as it does in the provider's own
  -- terms. Only 'hybrid' returns a fused rank score.
  case (select mode from params)
    when 'vector'  then f.similarity
    when 'lexical' then f.rank
    else f.rrf_score
  end                                                            as score
from fused f
join visible v on v.id = f.id
order by score desc nulls last, v.document_id, v.chunk_index
limit (select match_count from params);
$$;

comment on function public.search_document_chunks is
  'Owner-scoped chunk search. SECURITY INVOKER so RLS produces the top-k rather than filtering it. Modes: vector | lexical | hybrid (RRF).';

grant execute on function public.search_document_chunks(
  extensions.vector, text, uuid, uuid, integer, text, integer, text
) to authenticated, service_role;


-- ── deterministic warranty arithmetic ───────────────────────────────────────
-- Dates are arithmetic, not inference. The model is never asked "is this still
-- in warranty" — it is handed the answer this function computed from stored
-- fields, and told explicitly that coverage of a *specific fault* is a separate
-- question that only the documents can speak to.
create or replace function public.product_warranty_facts(p_product_id uuid)
returns table (
  product_id          uuid,
  purchase_date       date,
  warranty_months     integer,
  warranty_expires_at date,
  days_remaining      integer,
  status              text
)
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select
    p.id,
    p.purchase_date,
    p.warranty_months,
    p.warranty_expires_at,
    case when p.warranty_expires_at is null then null
         else (p.warranty_expires_at - current_date) end,
    case
      when p.warranty_expires_at is null then 'unknown'
      when p.warranty_expires_at < current_date then 'expired'
      when p.warranty_expires_at <= current_date + 30 then 'expiring'
      else 'active'
    end
  from public.products p
  where p.id = p_product_id;
$$;

comment on function public.product_warranty_facts is
  'Warranty window computed from stored fields. An active window is not evidence that a given fault is covered.';

grant execute on function public.product_warranty_facts(uuid) to authenticated, service_role;


-- ── the graph's private schema ─────────────────────────────────────────────
-- LangGraph's Postgres checkpointer creates and owns its own tables. They are
-- put in their own schema so that:
--
--   * they are visibly not part of the product schema, and
--   * `authenticated` has no access to them at all — a checkpoint is serialised
--     graph state, including retrieved document text, and the only legitimate
--     reader is the service that produced it.
--
-- Conversation ownership therefore is *not* enforced by RLS on these tables; it
-- is enforced by looking the thread up in public.ai_threads (which is under RLS)
-- before the graph is ever invoked or resumed. That check is in
-- services/ai/app/routers/chat.py and is covered by tests.
create schema if not exists ai_graph;

revoke all on schema ai_graph from public;
revoke usage on schema ai_graph from anon, authenticated;

comment on schema ai_graph is
  'LangGraph checkpoint tables. Service-only: thread ownership is checked against public.ai_threads before any graph run.';
