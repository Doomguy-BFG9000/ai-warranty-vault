"""Chunking behaviour: determinism, page provenance, and honest locators."""

from __future__ import annotations

from app.ingest.chunk import ExtractedUnit, chunk_units

PAGE_TEXT = (
    "Czyszczenie filtra pompy. Filtr pompy znajduje się za dolną klapką serwisową. "
    "Przed czyszczeniem odłącz pralkę od zasilania i zamknij zawór wody. "
    "Otwórz klapkę, podstaw płaskie naczynie i powoli odkręć korek filtra. "
    "Wyjmij wkład filtra i usuń zanieczyszczenia pod bieżącą wodą. "
    "Zalecamy czyszczenie filtra raz na dwa miesiące.\n\n"
    "Kod E17 oznacza problem z odprowadzaniem wody. Sprawdź filtr pompy oraz wąż odpływowy."
)


def test_chunking_is_deterministic():
    units = [ExtractedUnit(text=PAGE_TEXT, page_number=2)]
    first = chunk_units(units, target_tokens=40, overlap_tokens=8, min_tokens=5)
    second = chunk_units(units, target_tokens=40, overlap_tokens=8, min_tokens=5)
    assert [c.content_sha256 for c in first] == [c.content_sha256 for c in second]
    assert [c.chunk_index for c in first] == list(range(len(first)))


def test_chunks_never_span_pages():
    units = [
        ExtractedUnit(text=PAGE_TEXT, page_number=3),
        ExtractedUnit(text="Dane techniczne. Napięcie 220-240 V. Moc 2100 W.", page_number=4),
    ]
    chunks = chunk_units(units, target_tokens=40, overlap_tokens=8, min_tokens=5)

    # No chunk mixes text from both pages: page 4's distinctive string never appears
    # in a chunk labelled page 3, and vice versa.
    for chunk in chunks:
        if chunk.page_number == 3:
            assert "2100 W" not in chunk.content
        if chunk.page_number == 4:
            assert "filtra" not in chunk.content
    assert {c.page_number for c in chunks} == {3, 4}


def test_page_chunks_cite_the_page_and_plain_text_cites_characters():
    paged = chunk_units([ExtractedUnit(text=PAGE_TEXT, page_number=7)], target_tokens=200)
    assert all(chunk.locator == "page 7" for chunk in paged)

    # A .txt file has no pages. Inventing "page 1" would put a false number into a
    # citation, so the locator is a character range and page_number stays null.
    plain = chunk_units([ExtractedUnit(text=PAGE_TEXT)], target_tokens=40, overlap_tokens=8)
    assert all(chunk.page_number is None for chunk in plain)
    assert all(chunk.locator.startswith("characters ") for chunk in plain)


def test_overlap_is_applied_but_never_repeats_a_whole_chunk():
    chunks = chunk_units([ExtractedUnit(text=PAGE_TEXT, page_number=1)], target_tokens=40, overlap_tokens=15)
    assert len(chunks) > 1
    contents = [c.content for c in chunks]
    assert len(set(contents)) == len(contents)
    # Consecutive chunks share a boundary sentence, which is the point of overlap.
    assert any(
        chunks[i].char_start < chunks[i - 1].char_end for i in range(1, len(chunks))
    )


def test_an_oversized_segment_is_split_on_word_boundaries():
    long_line = " ".join(f"part-{n:04d}" for n in range(400))
    chunks = chunk_units([ExtractedUnit(text=long_line, page_number=1)], target_tokens=50, overlap_tokens=0)
    assert len(chunks) > 1
    for chunk in chunks:
        # Never split inside a token: every piece is a whole "part-NNNN".
        for word in chunk.content.split():
            assert word.startswith("part-") and len(word) == len("part-0000")


def test_empty_and_whitespace_units_produce_nothing():
    assert chunk_units([ExtractedUnit(text="   \n\n  ", page_number=1)]) == []
    assert chunk_units([]) == []
