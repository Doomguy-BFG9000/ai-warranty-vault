"""Token counting, with a deterministic fallback.

Chunk sizes are expressed in tokens, so the tokenizer is part of what a chunk
*is*. `tiktoken` is used when it is available — it is the tokenizer the embedding
provider actually bills and truncates by. It downloads its vocabulary on first
use, though, which means it is unavailable on a machine with no egress to
OpenAI's blob storage (CI sandboxes, air-gapped runs).

Rather than failing there, or silently producing differently sized chunks, the
fallback counter is used *and named*: `tokenizer_id()` goes into the index
version, so chunks counted one way are never searched alongside chunks counted
the other way. A reindex is the price of moving between the two, which is the
correct price.
"""

from __future__ import annotations

import functools
import logging
import math
import re

logger = logging.getLogger(__name__)

# The encoding used by text-embedding-3-* and the current Claude tokenizer's
# nearest public equivalent. Only used for measuring, never for splitting: the
# chunker splits on word and sentence boundaries, so no token is ever cut in half.
_ENCODING_NAME = "cl100k_base"

_WORD_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@functools.lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        logger.warning(
            "tiktoken unavailable (%s); using the approximate token counter. "
            "Chunk boundaries differ from a tiktoken run, which is why the "
            "tokenizer is part of the index version.",
            type(exc).__name__,
        )
        return None


@functools.lru_cache(maxsize=1)
def tokenizer_id() -> str:
    return f"tiktoken/{_ENCODING_NAME}" if _encoding() is not None else "approx/word-v1"


def count_tokens(text: str) -> int:
    """Number of tokens in `text`."""
    encoding = _encoding()
    if encoding is not None:
        return len(encoding.encode(text, disallowed_special=()))

    # Approximation: one token per short word or punctuation mark, and long words
    # (compounds, Polish inflections, part numbers) charged by length. Monotone in
    # text length, which is all the chunker needs, and stable across machines.
    total = 0
    for match in _WORD_RE.finditer(text):
        length = len(match.group(0))
        total += 1 if length <= 6 else math.ceil(length / 4)
    return total
