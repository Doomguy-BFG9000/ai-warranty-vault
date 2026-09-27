"""Service configuration.

Everything that varies between machines is an environment variable, and every
model name is one of them: an identifier that was current when this code was
written is not evidence that it is current now, or that a given key has access
to it. `scripts/preflight.py` makes one small live call per configured model so
that assumption is checked rather than trusted.

Secrets live here and nowhere else — never in the app bundle, never in git. The
mobile client only ever learns this service's base URL.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderMode = Literal["live", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── database ────────────────────────────────────────────────────────────
    # A single Postgres connection string. The service opens two *scopes* over
    # it rather than two credentials: request work runs as `authenticated` with
    # the caller's verified claims, so Row Level Security applies; the indexing
    # worker runs unscoped because it has no caller. See app/db.py.
    database_url: str = Field(
        default="postgresql://postgres@localhost:5432/postgres",
        description="Postgres DSN. Supabase: the project's direct connection string.",
    )
    db_privileged_role: str = Field(
        default="postgres",
        description="Role the worker/service scope runs as. Must own the tables or bypass RLS.",
    )
    db_pool_min: int = 1
    db_pool_max: int = 8
    db_statement_timeout_ms: int = 15_000

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: str = Field(default="http://127.0.0.1:54321")
    # Used only to read a document's bytes out of the private bucket during
    # indexing, when the caller's JWT is long gone. Never handed to a model.
    supabase_service_role_key: str | None = None
    # Legacy/local Supabase signs JWTs symmetrically (HS256); newer projects use
    # asymmetric keys published as JWKS. Both are supported; one must be set.
    supabase_jwt_secret: str | None = None
    supabase_jwks_url: str | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_issuer: str | None = None
    jwks_cache_seconds: int = 600

    # ── provider mode ───────────────────────────────────────────────────────
    # 'mock' is opt-in and loud. It never engages as a fallback: a provider
    # error in live mode surfaces as an error, because a silent downgrade to
    # deterministic fake embeddings would look like a working system while
    # quietly destroying retrieval quality.
    provider_mode: ProviderMode = "live"

    # ── embeddings ──────────────────────────────────────────────────────────
    # OpenAI text-embedding-3-large, truncated to 1024 dimensions.
    #
    # Why this one:
    #   * Documents here are Polish or English, often mixed, so a multilingual
    #     space is a requirement rather than a nicety. Of the widely available
    #     options this model is the strongest multilingual retriever OpenAI
    #     publishes, and it is a single well-documented endpoint rather than a
    #     second vendor to provision.
    #   * It supports native dimension truncation, and 1024 is not an arbitrary
    #     choice: pgvector indexes `vector` columns up to 2000 dimensions, so the
    #     model's native 3072 could not be indexed at all. 1024 keeps the index
    #     buildable and the table a third of the size.
    #   * Alternatives considered: Voyage (voyage-3 family) and Cohere
    #     embed-multilingual-v3 are both credible and both would need only this
    #     module plus a migration for the column width. The recorded
    #     embedding_model/embedding_dim/index_version on every chunk is what
    #     makes that swap safe instead of silently corrupting.
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 1024
    embedding_batch_size: int = 64
    openai_api_key: str | None = None

    # ── generation ──────────────────────────────────────────────────────────
    # Claude for answering and for reading scans. The project already depends on
    # Anthropic for receipt vision, so this adds no new vendor, and the same
    # model family handles grounded synthesis and tool selection.
    anthropic_api_key: str | None = None
    answer_model: str = "claude-sonnet-5"
    vision_model: str = "claude-sonnet-5"
    answer_max_tokens: int = 2048
    model_timeout_seconds: float = 60.0
    model_max_retries: int = 2

    # ── chunking ────────────────────────────────────────────────────────────
    # ~350 tokens is roughly one or two paragraphs of a warranty booklet, which
    # is the granularity a citation should have: "this clause on page 4", not
    # "somewhere on page 4". 60 tokens of overlap (~17%) means a clause that
    # straddles a boundary still appears whole in one of the two chunks.
    chunk_target_tokens: int = 350
    chunk_overlap_tokens: int = 60
    chunk_min_tokens: int = 24

    # ── retrieval ───────────────────────────────────────────────────────────
    retrieval_top_k: int = 8
    retrieval_candidates: int = 40
    retrieval_mode: Literal["vector", "lexical", "hybrid"] = "hybrid"
    # A floor on the fused score, calibrated against eval/questions.yaml rather
    # than guessed. It is a filter on obvious noise, not a confidence estimate:
    # a similarity is not a probability that the answer is in the chunk, which is
    # why groundedness is decided by the answering step and not by this number.
    retrieval_min_score: float = 0.012
    retrieval_min_vector_similarity: float = 0.18
    context_token_budget: int = 3_500

    # ── document limits ─────────────────────────────────────────────────────
    max_document_bytes: int = 15 * 1024 * 1024
    max_document_pages: int = 60
    # OCR is the expensive path: one vision call per page. Capped separately and
    # lower, and the user is told when a document was truncated.
    max_ocr_pages: int = 12
    ocr_page_dpi: int = 150

    # ── worker ──────────────────────────────────────────────────────────────
    worker_id: str = "local-worker"
    worker_poll_seconds: float = 2.0
    worker_concurrency: int = 2
    worker_lease_seconds: int = 300
    worker_backoff_base_seconds: int = 15

    # ── graph ───────────────────────────────────────────────────────────────
    graph_schema: str = "ai_graph"
    # Hard ceiling on tool-calling rounds. The graph is bounded by construction,
    # not by asking the model to stop.
    max_tool_steps: int = 4

    # ── http ────────────────────────────────────────────────────────────────
    cors_allow_origins: str = "*"
    request_timeout_seconds: float = 120.0

    @field_validator("embedding_dimensions")
    @classmethod
    def _dimensions_indexable(cls, value: int) -> int:
        # pgvector cannot index a `vector` wider than 2000, and the migration
        # pins the column at 1024. Catch a mismatch here rather than at the
        # first INSERT.
        if not 1 <= value <= 2000:
            raise ValueError("embedding_dimensions must be between 1 and 2000")
        return value

    @property
    def index_version(self) -> str:
        """Fingerprint of everything that changes what a chunk or vector means.

        Chunks are only ever compared within one index_version, so a change to
        the model, its width, or the chunker's parameters produces a new space
        rather than a corrupted one. Reindexing is then just "index under the
        new version and drop the old rows".
        """
        material = (
            f"chunk:{self.chunk_target_tokens}/{self.chunk_overlap_tokens}/{self.chunk_min_tokens}"
            f"|embed:{self.embedding_model}@{self.embedding_dimensions}"
        )
        digest = hashlib.sha256(material.encode()).hexdigest()[:8]
        return f"v1-{digest}"

    @property
    def is_mock(self) -> bool:
        return self.provider_mode == "mock"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
