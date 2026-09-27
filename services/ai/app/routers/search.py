"""Semantic and hybrid search over the caller's own documents."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.auth import Caller, current_caller
from app.db import fetch_one
from app.deps import Services, get_services
from app.providers import embed_query
from app.rag.retriever import apply_relevance_floor, search_chunks
from app.schemas import SearchHit, SearchRequest, SearchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["search"])

# Enough of a chunk to recognise the passage in a list; the whole chunk is one tap
# away on the document screen.
_SNIPPET_CHARS = 320


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> SearchResponse:
    settings = services.settings
    mode = payload.mode or settings.retrieval_mode
    top_k = payload.top_k or settings.retrieval_top_k
    embeddings = services.require_embeddings()

    vector = None
    if mode in ("vector", "hybrid"):
        async with services.telemetry.stage(
            "search.embed_query", user_id=caller.user_id, model=embeddings.model
        ):
            vector = await embed_query(embeddings, payload.query)

    async with services.db.user_scope(caller.claims) as conn:
        async with services.telemetry.stage(
            "search.query", user_id=caller.user_id, mode=mode, top_k=top_k
        ) as stage:
            hits = await search_chunks(
                conn,
                query_embedding=vector,
                query_text=payload.query,
                product_id=payload.product_id,
                document_id=payload.document_id,
                top_k=top_k,
                mode=mode,
                candidate_count=settings.retrieval_candidates,
                index_version=services.index_version,
            )
            stage.detail["hits"] = len(hits)

        # "No results" and "nothing to search" are different problems with
        # different fixes, so the answer says which one it is.
        indexed = await fetch_one(
            conn,
            """
            select count(*) as n
            from public.product_documents
            where status = 'ready'
              and index_version = %(index_version)s
              and (%(product_id)s::uuid is null or product_id = %(product_id)s::uuid)
            """,
            {"index_version": services.index_version, "product_id": payload.product_id},
        )

    filtered = apply_relevance_floor(hits, settings, mode=mode)

    return SearchResponse(
        mode=mode,
        top_k=top_k,
        index_version=services.index_version,
        no_documents=(indexed or {}).get("n", 0) == 0,
        hits=[
            SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                document_title=hit.document_title,
                product_id=hit.product_id,
                page_number=hit.page_number,
                locator=hit.locator,
                snippet=hit.content[:_SNIPPET_CHARS],
                score=hit.score,
                vector_similarity=hit.vector_similarity,
                lexical_rank=hit.lexical_rank,
                vector_position=hit.vector_position,
                lexical_position=hit.lexical_position,
            )
            for hit in filtered
        ],
    )
