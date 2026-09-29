"""Model and embedding providers, behind LangChain interfaces.

LangChain is used here as the integration layer it is good at being: one
`Embeddings` implementation and one chat model, both swappable, both with
retries and timeouts configured in one place. Nothing in the rest of the service
knows which vendor is behind them.

Mock mode is explicit and never automatic. `PROVIDER_MODE=mock` selects
deterministic stand-ins; a failure in live mode raises `ProviderError` and stays
raised. This matters more than it looks: if a provider outage silently swapped in
hash-based vectors, indexing would "succeed", the index would fill with garbage
that is indistinguishable from real vectors at the SQL level, and the only
symptom would be answers that quietly stop being relevant.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.config import Settings
from app.errors import ProviderError, ProviderNotConfigured

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)


# ── mock embeddings ─────────────────────────────────────────────────────────
class HashingEmbeddings(Embeddings):
    """Deterministic bag-of-words embeddings for offline tests.

    Each token is hashed into a bucket and the vector is L2-normalised, so cosine
    similarity reflects vocabulary overlap. That is enough to exercise the vector
    SQL, the RLS-scoped top-k, chunk identity and the fusion arithmetic without a
    network call, and it is reproducible across machines.

    It is *not* a semantic model. It cannot match a paraphrase, so no conclusion
    about retrieval quality may be drawn from a run in this mode — which is why
    the evaluation script refuses to report quality numbers unless it is running
    against the real provider.
    """

    name = "mock/hashing-bow"

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            # An all-zero vector has no direction, so cosine distance against it
            # is undefined. Park it on a fixed basis vector instead.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


# ── factory ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EmbeddingHandle:
    embeddings: Embeddings
    model: str
    dimensions: int


def build_embeddings(settings: Settings) -> EmbeddingHandle:
    if settings.is_mock:
        logger.warning(
            "PROVIDER_MODE=mock: using deterministic hashing embeddings. "
            "Retrieval quality numbers from this mode are meaningless."
        )
        return EmbeddingHandle(
            embeddings=HashingEmbeddings(settings.embedding_dimensions),
            model=f"{HashingEmbeddings.name}@{settings.embedding_dimensions}",
            dimensions=settings.embedding_dimensions,
        )

    if not settings.openai_api_key:
        raise ProviderNotConfigured("OPENAI_API_KEY (embeddings)")

    from langchain_openai import OpenAIEmbeddings

    return EmbeddingHandle(
        embeddings=OpenAIEmbeddings(
            model=settings.embedding_model,
            # Matryoshka truncation, requested from the provider rather than
            # sliced locally, so the returned vector is renormalised properly.
            dimensions=settings.embedding_dimensions,
            api_key=settings.openai_api_key,
            timeout=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
            chunk_size=settings.embedding_batch_size,
        ),
        model=f"openai/{settings.embedding_model}@{settings.embedding_dimensions}",
        dimensions=settings.embedding_dimensions,
    )


def build_chat_model(
    settings: Settings,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    effort: str | None = None,
) -> BaseChatModel:
    """A chat model configured for grounded work.

    Deliberately does not set `temperature`, `top_p` or `top_k`. This model
    generation removed them: sending any of the three returns

        400 invalid_request_error: `temperature` is deprecated for this model.

    Only `claude-fable-5*` is special-cased inside langchain-anthropic, so on every
    other current model an unwanted `temperature` reaches the API and the whole
    request fails. Depth is controlled with `output_config.effort` instead.
    """
    if settings.is_mock:
        from app.mock_chat import ScriptedChatModel

        return ScriptedChatModel()

    if not settings.anthropic_api_key:
        raise ProviderNotConfigured("ANTHROPIC_API_KEY")

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=model or settings.answer_model,
        max_tokens=max_tokens or settings.answer_max_tokens,
        output_config={"effort": effort or settings.model_effort},
        timeout=settings.model_timeout_seconds,
        max_retries=settings.model_max_retries,
        api_key=settings.anthropic_api_key,
    )


def describe_provider_failure(exc: BaseException) -> str:
    """Exception type plus the provider's own message, trimmed.

    The type alone is not diagnosable: OpenAI raises `RateLimitError` both for "you
    are going too fast" and for "this key has no credit" (`insufficient_quota`), and
    those need opposite responses from whoever is reading the log. The provider's
    message says which, so it is kept — trimmed, because some providers return a
    whole HTML error page.
    """
    detail = str(exc).strip().replace("\n", " ")
    if not detail:
        return type(exc).__name__
    return f"{type(exc).__name__}: {detail[:300]}"


async def embed_documents(handle: EmbeddingHandle, texts: list[str]) -> list[list[float]]:
    try:
        vectors = await handle.embeddings.aembed_documents(texts)
    except Exception as exc:  # noqa: BLE001 - normalised into ProviderError
        raise ProviderError(f"embedding call failed: {describe_provider_failure(exc)}") from exc

    if len(vectors) != len(texts):
        raise ProviderError("embedding provider returned the wrong number of vectors", retryable=False)
    for vector in vectors:
        if len(vector) != handle.dimensions:
            raise ProviderError(
                f"embedding provider returned {len(vector)} dimensions, expected {handle.dimensions}",
                retryable=False,
            )
    return vectors


async def embed_query(handle: EmbeddingHandle, text: str) -> list[float]:
    try:
        vector = await handle.embeddings.aembed_query(text)
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"embedding call failed: {describe_provider_failure(exc)}") from exc
    if len(vector) != handle.dimensions:
        raise ProviderError(
            f"embedding provider returned {len(vector)} dimensions, expected {handle.dimensions}",
            retryable=False,
        )
    return vector


def usage_from_response(message: Any) -> tuple[int | None, int | None]:
    """Provider-reported token counts, or (None, None).

    Estimates are not substituted. A number in the telemetry table should be a
    measurement; a guess dressed up as one makes cost reporting fiction.
    """
    metadata = getattr(message, "usage_metadata", None)
    if isinstance(metadata, dict):
        return metadata.get("input_tokens"), metadata.get("output_tokens")
    response_metadata = getattr(message, "response_metadata", None) or {}
    usage = response_metadata.get("usage") or {}
    if usage:
        return usage.get("input_tokens"), usage.get("output_tokens")
    return None, None
