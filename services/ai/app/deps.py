"""Process-wide singletons and the FastAPI dependencies that hand them out.

Everything expensive is created once at startup: the connection pools, the
embedding client, the graph checkpointer. A request gets references, not new
clients — which also means a misconfiguration fails at boot with a clear message
rather than on the first user who happens to trigger it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.db import Database
from app.errors import ProviderError
from app.ingest.indexer import index_version_for
from app.providers import EmbeddingHandle, build_embeddings
from app.telemetry import Telemetry

logger = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database
    telemetry: Telemetry
    embeddings: EmbeddingHandle | None
    embeddings_error: str | None
    checkpointer: object | None
    index_version: str

    def require_embeddings(self) -> EmbeddingHandle:
        if self.embeddings is None:
            raise ProviderError(
                self.embeddings_error or "embeddings are not configured",
                code="provider_not_configured",
                retryable=False,
            )
        return self.embeddings


async def build_services(settings: Settings | None = None) -> Services:
    settings = settings or get_settings()
    db = Database(settings)
    await db.open()

    embeddings: EmbeddingHandle | None = None
    embeddings_error: str | None = None
    try:
        embeddings = build_embeddings(settings)
    except ProviderError as exc:
        # The service still starts. Listing documents, reading a transcript and
        # deleting a document all work without an embedding provider; only search
        # and indexing need one, and those say so explicitly instead of the whole
        # API being down.
        embeddings_error = str(exc)
        logger.warning("embeddings unavailable: %s", exc)

    return Services(
        settings=settings,
        db=db,
        telemetry=Telemetry(db),
        embeddings=embeddings,
        embeddings_error=embeddings_error,
        checkpointer=None,
        index_version=index_version_for(settings),
    )


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_db(services: Services = Depends(get_services)) -> Database:
    return services.db
