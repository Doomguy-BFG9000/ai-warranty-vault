"""The synthetic corpus, and how it maps onto products and users.

Shared by the test suite and the evaluation script so both exercise the same
documents. Everything in it is invented — "AquaWash 8000", "BrewMaster 300" — so
no real manufacturer's terms are being quoted or implied.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

CORPUS_DIR = pathlib.Path(__file__).resolve().parent / "corpus"
QUESTIONS_PATH = pathlib.Path(__file__).resolve().parent / "questions.yaml"


@dataclass(frozen=True)
class CorpusDocument:
    slug: str
    title: str
    kind: str
    # Which of the seeded products it belongs to: 'washer' or 'coffee'.
    product: str
    filename: str

    @property
    def text(self) -> str:
        return (CORPUS_DIR / self.filename).read_text(encoding="utf-8")


DOCUMENTS: tuple[CorpusDocument, ...] = (
    CorpusDocument(
        slug="aquawash-8000-manual-pl",
        title="AquaWash 8000 — instrukcja obsługi",
        kind="manual",
        product="washer",
        filename="aquawash-8000-manual-pl.txt",
    ),
    CorpusDocument(
        slug="aquawash-8000-warranty-pl",
        title="AquaWash 8000 — karta gwarancyjna",
        kind="warranty_card",
        product="washer",
        filename="aquawash-8000-warranty-pl.txt",
    ),
    CorpusDocument(
        slug="aquawash-8000-service-terms-en",
        title="AquaWash Service Terms (EU)",
        kind="service_terms",
        product="washer",
        filename="aquawash-8000-service-terms-en.txt",
    ),
    CorpusDocument(
        slug="aquawash-8000-injection-probe-pl",
        title="AquaWash 8000 — załącznik serwisowy",
        kind="other",
        product="washer",
        filename="aquawash-8000-injection-probe-pl.txt",
    ),
    CorpusDocument(
        slug="brewmaster-300-manual-en",
        title="BrewMaster 300 — user guide",
        kind="manual",
        product="coffee",
        filename="brewmaster-300-manual-en.txt",
    ),
    CorpusDocument(
        slug="brewmaster-300-warranty-en",
        title="BrewMaster 300 — warranty statement",
        kind="warranty_card",
        product="coffee",
        filename="brewmaster-300-warranty-en.txt",
    ),
)

BY_SLUG = {document.slug: document for document in DOCUMENTS}


def load_questions() -> list[dict]:
    import yaml

    data = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))
    return list(data["questions"])
