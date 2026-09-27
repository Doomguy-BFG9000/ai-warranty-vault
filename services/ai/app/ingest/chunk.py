"""Deterministic, page-anchored chunking.

Three properties matter, in this order.

**Provenance.** A chunk belongs to exactly one page. Text from page 3 and page 4
is never concatenated into one chunk, because a citation that says "page 3" has
to be true. Plain text has no pages, so its chunks carry a character range
instead of an invented page number.

**Determinism.** Chunking is a pure function of (text, page, parameters). The
same document indexed twice produces byte-identical chunks, which is what makes
re-running an indexing job a no-op instead of a duplicate, and what makes the
"change the chunk size and compare retrieval" exercise in the interview notes
actually comparable.

**Boundary respect.** Splits happen at paragraph, then sentence, then word
boundaries — never mid-word. An embedding of a fragment that starts mid-word is
measurably worse, and a quoted citation that starts mid-word looks broken.

The size is a judgement call, not a law: ~350 tokens is one or two paragraphs of a
warranty booklet, which keeps a single clause ("Uszkodzenia mechaniczne… nie są
objęte gwarancją") intact inside one chunk while keeping citations specific
enough to be useful. 60 tokens of overlap means a clause that straddles a
boundary still appears whole in one of the two neighbours.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.ingest.tokens import count_tokens

# Paragraph break: a blank line, or a run of newlines.
_PARAGRAPH_RE = re.compile(r"\n\s*\n+")
# Sentence-ish break. Includes the newline case so list items and table rows,
# which rarely end in a full stop, are still separable.
_SENTENCE_RE = re.compile(r"(?<=[.!?…:;])\s+|\n+")
_WS_RUN_RE = re.compile(r"[ \t ]+")


@dataclass(frozen=True)
class ExtractedUnit:
    """One page of a document, or the whole body of a page-less one."""

    text: str
    page_number: int | None = None


@dataclass(frozen=True)
class Chunk:
    chunk_index: int
    page_number: int | None
    locator: str
    content: str
    token_count: int
    char_start: int
    char_end: int

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Segment:
    text: str
    start: int
    end: int
    tokens: int


def _normalise(text: str) -> str:
    """Collapse horizontal whitespace, keep line structure.

    PDF text extraction is full of column-alignment padding. Collapsing it makes
    chunk sizes reflect content rather than layout, and keeps the same document
    from chunking differently after a cosmetic re-export. Line breaks are kept
    because they carry the list/table structure the sentence splitter relies on.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RUN_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _segments(text: str) -> list[_Segment]:
    """Split into the smallest units the packer is allowed to move around."""
    out: list[_Segment] = []
    for paragraph_match in _split_with_offsets(text, _PARAGRAPH_RE):
        para_text, para_start = paragraph_match
        for sentence_text, offset in _split_with_offsets(para_text, _SENTENCE_RE):
            stripped = sentence_text.strip()
            if not stripped:
                continue
            lead = sentence_text.index(stripped[0]) if stripped else 0
            start = para_start + offset + lead
            out.append(
                _Segment(
                    text=stripped,
                    start=start,
                    end=start + len(stripped),
                    tokens=count_tokens(stripped),
                )
            )
    return out


def _split_with_offsets(text: str, pattern: re.Pattern[str]) -> list[tuple[str, int]]:
    pieces: list[tuple[str, int]] = []
    cursor = 0
    for match in pattern.finditer(text):
        pieces.append((text[cursor : match.start()], cursor))
        cursor = match.end()
    pieces.append((text[cursor:], cursor))
    return [(piece, offset) for piece, offset in pieces if piece.strip()]


def _split_oversized(segment: _Segment, target: int) -> list[_Segment]:
    """Break a single over-long segment on word boundaries.

    Hit by tables, addresses and OCR output that lost its punctuation. Splitting
    on words rather than characters keeps every piece embeddable.
    """
    words = list(re.finditer(r"\S+", segment.text))
    if not words:
        return [segment]

    out: list[_Segment] = []
    buffer_start = words[0].start()
    buffer_end = buffer_start
    tokens = 0

    for word in words:
        word_tokens = count_tokens(word.group(0))
        if tokens and tokens + word_tokens > target:
            piece = segment.text[buffer_start:buffer_end].strip()
            if piece:
                out.append(
                    _Segment(
                        text=piece,
                        start=segment.start + buffer_start,
                        end=segment.start + buffer_start + len(piece),
                        tokens=tokens,
                    )
                )
            buffer_start = word.start()
            tokens = 0
        buffer_end = word.end()
        tokens += word_tokens

    piece = segment.text[buffer_start:buffer_end].strip()
    if piece:
        out.append(
            _Segment(
                text=piece,
                start=segment.start + buffer_start,
                end=segment.start + buffer_start + len(piece),
                tokens=tokens,
            )
        )
    return out


def _overlap_carry(buffer: list[_Segment], overlap_tokens: int) -> list[_Segment]:
    """The segments that start the next chunk, so a clause is never only half-seen.

    Whole segments are preferred: a chunk that begins at a sentence boundary embeds
    better and quotes better. But documents whose sentences are longer than the
    overlap budget would then get no overlap at all — and those are exactly the
    documents (dense legal terms, long instructions) where a clause is most likely
    to straddle a boundary. So when no whole segment fits, the trailing *words* of
    the last segment are carried instead, still on word boundaries.
    """
    if overlap_tokens <= 0 or not buffer:
        return []

    carry: list[_Segment] = []
    carried = 0
    # `buffer[:-1]`: the final segment of the emitted chunk is skipped when whole
    # segments are being carried, so the next chunk can never be a repeat of this one.
    for previous in reversed(buffer[:-1]):
        if carried + previous.tokens > overlap_tokens:
            break
        carry.insert(0, previous)
        carried += previous.tokens
    if carry:
        return carry

    return _tail_words(buffer[-1], overlap_tokens)


def _tail_words(segment: _Segment, budget_tokens: int) -> list[_Segment]:
    """Trailing words of a segment, up to the token budget."""
    words = list(re.finditer(r"\S+", segment.text))
    if len(words) < 2:
        return []

    tokens = 0
    start_index = len(words)
    for index in range(len(words) - 1, 0, -1):
        cost = count_tokens(words[index].group(0))
        if tokens + cost > budget_tokens:
            break
        tokens += cost
        start_index = index
    if start_index >= len(words):
        return []

    start = words[start_index].start()
    piece = segment.text[start:].strip()
    if not piece:
        return []
    return [
        _Segment(
            text=piece,
            start=segment.start + start,
            end=segment.start + start + len(piece),
            tokens=tokens,
        )
    ]


def _locator(page_number: int | None, char_start: int, char_end: int) -> str:
    if page_number is not None:
        return f"page {page_number}"
    # 1-based and inclusive, because it is read by a person, not sliced by code.
    return f"characters {char_start + 1}–{char_end}"


def chunk_units(
    units: list[ExtractedUnit],
    *,
    target_tokens: int = 350,
    overlap_tokens: int = 60,
    min_tokens: int = 24,
) -> list[Chunk]:
    """Chunk a document. `chunk_index` is global and in reading order."""
    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive")
    if not 0 <= overlap_tokens < target_tokens:
        raise ValueError("overlap_tokens must be >= 0 and smaller than target_tokens")

    chunks: list[Chunk] = []
    index = 0

    for unit in units:
        text = _normalise(unit.text)
        if not text:
            continue

        segments: list[_Segment] = []
        for segment in _segments(text):
            if segment.tokens > target_tokens:
                segments.extend(_split_oversized(segment, target_tokens))
            else:
                segments.append(segment)
        if not segments:
            continue

        unit_chunks: list[Chunk] = []
        buffer: list[_Segment] = []
        buffer_tokens = 0
        position = 0

        while position < len(segments):
            segment = segments[position]
            buffer.append(segment)
            buffer_tokens += segment.tokens
            position += 1

            if buffer_tokens >= target_tokens:
                unit_chunks.append(_emit(buffer, unit.page_number, 0))
                carry = _overlap_carry(buffer, overlap_tokens)
                buffer = carry
                buffer_tokens = sum(segment.tokens for segment in carry)

        if buffer:
            tail = _emit(buffer, unit.page_number, 0)
            # A short tail is folded into the previous chunk of the *same page*
            # rather than stored as a stub nobody would ever retrieve.
            if tail.token_count < min_tokens and unit_chunks:
                previous = unit_chunks[-1]
                merged_content = f"{previous.content}\n{tail.content}".strip()
                unit_chunks[-1] = Chunk(
                    chunk_index=previous.chunk_index,
                    page_number=previous.page_number,
                    locator=_locator(unit.page_number, previous.char_start, tail.char_end),
                    content=merged_content,
                    token_count=count_tokens(merged_content),
                    char_start=previous.char_start,
                    char_end=tail.char_end,
                )
            else:
                unit_chunks.append(tail)

        for chunk in unit_chunks:
            chunks.append(
                Chunk(
                    chunk_index=index,
                    page_number=chunk.page_number,
                    locator=chunk.locator,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                )
            )
            index += 1

    return chunks


def _emit(buffer: list[_Segment], page_number: int | None, _index: int) -> Chunk:
    content = " ".join(segment.text for segment in buffer).strip()
    char_start = buffer[0].start
    char_end = buffer[-1].end
    return Chunk(
        chunk_index=0,  # assigned globally by the caller
        page_number=page_number,
        locator=_locator(page_number, char_start, char_end),
        content=content,
        token_count=count_tokens(content),
        char_start=char_start,
        char_end=char_end,
    )
