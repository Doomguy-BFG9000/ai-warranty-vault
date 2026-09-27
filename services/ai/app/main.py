"""The AI service.

A small FastAPI app next to the existing Expo + Supabase project. It does not
replace anything: receipt extraction stays in the Deno Edge Function, the mobile
client stays Expo, Postgres stays the same Postgres. What is added here is the part
that needed a Python ecosystem — LangChain for the model and embedding
integrations, LangGraph for a durable stateful flow, pgvector for retrieval.

Run it with:

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

and the indexing worker alongside it:

    python -m app.ingest.worker
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import assert_embedding_column_width
from app.deps import build_services
from app.errors import ProviderError, provider_unavailable
from app.ingest.tokens import tokenizer_id
from app.routers import chat, debug, documents, search
from app.telemetry import new_operation_id

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = get_settings()
    services = await build_services(settings)
    app.state.services = services

    # Fail fast on the one misconfiguration that would otherwise be invisible:
    # vectors of the wrong width written into the wrong-width column.
    await assert_embedding_column_width(services.db, settings.embedding_dimensions)

    # The checkpointer owns its tables and creates them on first use. Doing it at
    # startup means a cold first request does not pay for DDL — and that a
    # permissions problem on the graph schema surfaces at boot.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pool = await services.db.graph_pool()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    services.checkpointer = checkpointer

    logger.info(
        "ai service ready: provider_mode=%s answer_model=%s embeddings=%s index_version=%s tokenizer=%s",
        settings.provider_mode,
        settings.answer_model,
        services.embeddings.model if services.embeddings else "unavailable",
        services.index_version,
        tokenizer_id(),
    )
    try:
        yield
    finally:
        await services.db.close()


app = FastAPI(
    title="AI Warranty Vault — document assistant",
    version="1.0.0",
    summary="Document indexing, retrieval and a human-approved claim drafting flow.",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["authorization", "content-type", "apikey"],
)


@app.middleware("http")
async def operation_id_middleware(request: Request, call_next):
    """One id per request, echoed back so a user report can be found in the log."""
    operation_id = new_operation_id()
    response = await call_next(request)
    response.headers["X-Operation-Id"] = str(operation_id)
    return response


@app.exception_handler(ProviderError)
async def provider_error_handler(_request: Request, exc: ProviderError) -> JSONResponse:
    # Surfaced as 503, never swallowed into a fabricated answer.
    http = provider_unavailable(exc)
    return JSONResponse(status_code=http.status_code, content={"detail": http.detail})


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/meta", tags=["meta"])
async def meta(request: Request) -> dict[str, object]:
    """Configuration the client and the docs both need. No secrets."""
    services = request.app.state.services
    settings = services.settings
    return {
        "provider_mode": settings.provider_mode,
        "answer_model": settings.answer_model,
        "vision_model": settings.vision_model,
        "embedding_model": services.embeddings.model if services.embeddings else None,
        "embedding_dimensions": settings.embedding_dimensions,
        "tokenizer": tokenizer_id(),
        "index_version": services.index_version,
        "retrieval": {
            "mode": settings.retrieval_mode,
            "top_k": settings.retrieval_top_k,
            "candidates": settings.retrieval_candidates,
            "min_score": settings.retrieval_min_score,
            "context_token_budget": settings.context_token_budget,
        },
        "chunking": {
            "target_tokens": settings.chunk_target_tokens,
            "overlap_tokens": settings.chunk_overlap_tokens,
        },
        "limits": {
            "max_document_bytes": settings.max_document_bytes,
            "max_document_pages": settings.max_document_pages,
            "max_ocr_pages": settings.max_ocr_pages,
            "max_tool_steps": settings.max_tool_steps,
        },
    }


app.include_router(documents.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(debug.router)
