-- ─────────────────────────────────────────────────────────────────────────────
-- Retrieval: a per-document cap inside the top-k
--
-- `search_document_chunks` gains `p_per_document_limit`. It replaces the eight
-- argument version rather than overloading it: every argument has a default, so
-- two versions would be an ambiguous call for any caller that omits the tail.
-- Hence the explicit drop — Postgres would otherwise keep both.
--
-- Everything above the final select is unchanged from
-- 20260927000010_ai_search_functions.sql; the cap is the last stage before the
-- limit, and is inert when the argument is null.
-- ─────────────────────────────────────────────────────────────────────────────

drop function if exists public.search_document_chunks(
  extensions.vector(1024), text, uuid, uuid, integer, text, integer, text
);

create or replace function public.search_document_chunks(
  p_query_embedding extensions.vector(1024),
  p_query_text      text                  default null,
  p_product_id      uuid                  default null,
  p_document_id     uuid                  default null,
  p_match_count     integer               default 8,
  p_mode            text                  default 'hybrid',
  p_candidate_count integer               default 40,
  p_index_version   text                  default null,
  p_per_document_limit integer            default null
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
    case when coalesce(p_per_document_limit, 0) > 0
         then least(p_per_document_limit, 50) end                as per_document_limit,
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
),
scored as (
  select
    v.id,
    v.document_id,
    v.product_id,
    v.page_number,
    v.locator,
    v.content,
    v.token_count,
    v.chunk_index,
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
    end                                                          as score
  from fused f
  join visible v on v.id = f.id
),
-- Per-document cap, applied while choosing the top-k rather than after it.
--
-- Why this exists: a long, densely relevant document wins every one of the eight
-- slots, and the second document on the same subject never reaches the model at
-- all. The visible symptom was a contradiction reported as a plain answer — the
-- conflict gate requires two documents, and only one had been retrieved, so the
-- assistant confidently stated one side of a disagreement it had no way to see.
-- Trimming after the limit cannot fix that: the rows are already gone. Ranking
-- within each document first and then taking the global top-k means a second
-- document's best passage displaces a first document's fourth-best.
--
-- A null or non-positive cap disables it, which is what the eval harness uses to
-- measure the cap's cost against uncapped retrieval on the same corpus.
capped as (
  select
    s.*,
    case when (select per_document_limit from params) is null then 1
         else row_number() over (
                partition by s.document_id
                order by s.score desc nulls last, s.chunk_index
              )
    end as document_rank
  from scored s
)
select
  id,
  document_id,
  product_id,
  page_number,
  locator,
  content,
  token_count,
  similarity,
  rank,
  vector_position,
  lexical_position,
  score
from capped
where document_rank <= coalesce((select per_document_limit from params), 1)
order by score desc nulls last, document_id, chunk_index
limit (select match_count from params);
$$;

comment on function public.search_document_chunks is
  'Owner-scoped chunk search. SECURITY INVOKER so RLS produces the top-k rather than filtering it. Modes: vector | lexical | hybrid (RRF). p_per_document_limit caps how many chunks one document may contribute, so a second document can reach the answer.';

grant execute on function public.search_document_chunks(
  extensions.vector(1024), text, uuid, uuid, integer, text, integer, text, integer
) to authenticated, service_role;
