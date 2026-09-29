"""Retrieval.

The SQL lives in `public.search_document_chunks` (see the migration) and this
module is the thin, typed layer over it plus the two policies that do not belong
in SQL: the relevance floor and the context budget.

Why the search is a SECURITY INVOKER SQL function rather than a query built here:
so that the ownership predicate is part of the plan that produces the top-k. If
this module fetched a global top-k and then dropped rows that belong to other
users, a vault with one document would compete for slots against every other
account's documents — and one forgotten filter would be a data leak rather than a
ranking bug.

On the relevance floor: a cosine similarity is not a probability that the answer is
in the chunk. The floor here is calibrated against `eval/questions.yaml` and its
only job is to keep obvious noise out of the prompt. Whether the retrieved text
actually supports an answer is decided afterwards, by the answering step, which is
allowed to say "no".
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from app.config import Settings
from app.db import Database, fetch_all
from app.providers import EmbeddingHandle, embed_query

logger = logging.getLogger(__name__)

SearchMode = Literal["vector", "lexical", "hybrid"]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    product_id: uuid.UUID
    page_number: int | None
    locator: str
    content: str
    token_count: int
    score: float
    vector_similarity: float | None
    lexical_rank: float | None
    vector_position: int | None
    lexical_position: int | None
    document_title: str | None = None
    document_kind: str | None = None

    def as_source(self, label: str) -> dict[str, Any]:
        """The citation record stored on a message. Server-built, in every field."""
        return {
            "label": label,
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "page_number": self.page_number,
            "locator": self.locator,
        }


# Every argument is cast explicitly. Without the casts, psycopg infers
# `double precision[]` for the embedding and `smallint` for the counts, and
# Postgres then cannot pick the overload — a confusing "function does not exist"
# for a function that plainly does.
_SEARCH_SQL = """
select s.*, d.title as document_title, d.kind::text as document_kind
from public.search_document_chunks(
       %(embedding)s::extensions.vector,
       %(query_text)s::text,
       %(product_id)s::uuid,
       %(document_id)s::uuid,
       %(match_count)s::int,
       %(mode)s::text,
       %(candidate_count)s::int,
       %(index_version)s::text,
       %(per_document_limit)s::int
     ) s
join public.product_documents d on d.id = s.document_id
order by s.score desc nulls last
"""


async def search_chunks(
    conn,
    *,
    query_embedding: list[float] | None,
    query_text: str | None,
    product_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    top_k: int = 8,
    mode: SearchMode = "hybrid",
    candidate_count: int = 40,
    index_version: str | None = None,
    per_document_limit: int | None = None,
) -> list[RetrievedChunk]:
    """Run the owner-scoped search. `conn` must already be in a user scope."""
    rows = await fetch_all(
        conn,
        _SEARCH_SQL,
        {
            "embedding": query_embedding,
            "query_text": query_text,
            "product_id": product_id,
            "document_id": document_id,
            "match_count": top_k,
            "mode": mode,
            "candidate_count": candidate_count,
            "index_version": index_version,
            "per_document_limit": per_document_limit,
        },
    )
    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            product_id=row["product_id"],
            page_number=row["page_number"],
            locator=row["locator"],
            content=row["content"],
            token_count=row["token_count"],
            score=float(row["score"]) if row["score"] is not None else 0.0,
            vector_similarity=(
                float(row["vector_similarity"]) if row["vector_similarity"] is not None else None
            ),
            lexical_rank=float(row["lexical_rank"]) if row["lexical_rank"] is not None else None,
            vector_position=row["vector_position"],
            lexical_position=row["lexical_position"],
            document_title=row["document_title"],
            document_kind=row["document_kind"],
        )
        for row in rows
    ]


def apply_relevance_floor(
    chunks: list[RetrievedChunk], settings: Settings, *, mode: SearchMode
) -> list[RetrievedChunk]:
    """Drop obvious noise.

    In hybrid mode the score is an RRF rank score, whose scale depends only on the
    fusion constant, so a fixed floor is meaningful. In vector mode the score *is*
    the cosine similarity, so the similarity floor applies instead. Keeping the two
    separate avoids the usual mistake of comparing an RRF score against a
    similarity threshold.
    """
    if mode == "vector":
        floor = settings.retrieval_min_vector_similarity
        return [c for c in chunks if c.score >= floor]
    if mode == "lexical":
        # ts_rank_cd has no natural scale; anything the SQL matched at all is a
        # literal hit, so there is nothing sensible to threshold on here.
        return list(chunks)
    return [c for c in chunks if c.score >= settings.retrieval_min_score]


def fit_context(chunks: list[RetrievedChunk], budget_tokens: int) -> list[RetrievedChunk]:
    """Take chunks in rank order until the token budget runs out.

    Truncating a chunk is not an option: half a clause reads like a whole clause
    and would be cited as one. Better to carry fewer, complete sources.
    """
    kept: list[RetrievedChunk] = []
    used = 0
    for chunk in chunks:
        if kept and used + chunk.token_count > budget_tokens:
            continue
        kept.append(chunk)
        used += chunk.token_count
    return kept


class ProductDocumentRetriever(BaseRetriever):
    """LangChain retriever over one user's documents.

    Bound to a `Database` and a *verified* claims dict, so every query it runs is
    inside that user's RLS scope. There is no constructor parameter for "which
    user", only "which verified caller" — the distinction is what stops a caller
    from being chosen by anything other than a validated token.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    db: Database
    settings: Settings
    embeddings: EmbeddingHandle
    claims: dict[str, Any]
    index_version: str
    product_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    mode: SearchMode = "hybrid"
    top_k: int | None = None

    async def retrieve(self, query: str) -> list[RetrievedChunk]:
        top_k = self.top_k or self.settings.retrieval_top_k
        vector: list[float] | None = None
        if self.mode in ("vector", "hybrid"):
            vector = await embed_query(self.embeddings, query)

        async with self.db.user_scope(self.claims) as conn:
            chunks = await search_chunks(
                conn,
                query_embedding=vector,
                query_text=query,
                product_id=self.product_id,
                document_id=self.document_id,
                top_k=top_k,
                mode=self.mode,
                candidate_count=self.settings.retrieval_candidates,
                per_document_limit=self.settings.retrieval_per_document,
                index_version=self.index_version,
            )

        filtered = apply_relevance_floor(chunks, self.settings, mode=self.mode)
        return fit_context(filtered, self.settings.context_token_budget)

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        chunks = await self.retrieve(query)
        return [
            Document(
                page_content=chunk.content,
                metadata={
                    "chunk_id": str(chunk.chunk_id),
                    "document_id": str(chunk.document_id),
                    "document_title": chunk.document_title,
                    "page_number": chunk.page_number,
                    "locator": chunk.locator,
                    "score": chunk.score,
                },
            )
            for chunk in chunks
        ]

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        # Everything in this service is async; a sync path would need a second
        # connection pool and would only exist to be misused.
        raise NotImplementedError("Use the async interface: aget_relevant_documents / retrieve")
