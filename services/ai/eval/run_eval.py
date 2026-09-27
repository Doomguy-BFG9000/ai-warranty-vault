"""Evaluate retrieval and answering against the labelled question set.

    python -m eval.run_eval                  # retrieval only, all three modes
    python -m eval.run_eval --answers        # also run the answering step
    python -m eval.run_eval --mode hybrid --top-k 5

What is measured, and what each number does and does not mean.

**hit@k** — for a question with labelled evidence, whether *any* labelled phrase
appears in the top-k. This is the number that tells you whether the answer was even
reachable.

**recall@k** — the fraction of a question's labelled phrases present in the top-k.
Stricter than hit@k, and the one that moves when chunking changes: a phrase that
straddles a chunk boundary can be reachable as a fragment but not as a whole.

**leakage** — retrieval of a phrase labelled `forbidden_evidence`, asked by a user
who does not own the document containing it. Any non-zero value is a failure, not a
score. It is reported separately from every average so it cannot be diluted.

**citation validity** (with `--answers`) — every citation resolves to a chunk that
was in the retrieved context. Because that context came from an RLS-scoped query,
this doubles as a check that no answer ever cites another account's text.

**grounded** — at least one *cited* chunk contains a labelled phrase. A weaker claim
than "the answer is correct", and deliberately so: it catches the failure that
matters most, an answer that cites a source which does not actually support it. It
does not catch a wrong reading of the right source.

**abstained correctly** — a `no_answer` question answered with `no_answer`, and a
`conflict` question answered as a conflict. The existence of a citation is not
treated as evidence of quality, and neither is the model's own confidence: the model
is never asked how sure it is, because that number would not mean anything.

In `PROVIDER_MODE=mock` every quality figure is marked `quality_meaningful: false`
and printed with a warning. Mock embeddings are hash-based bag-of-words: they cannot
match a paraphrase, so a low recall there says nothing about the real model. The
mock run still checks the plumbing — access scoping, citation resolution, abstention
and the leakage assertion all work, and are worth running in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SERVICE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT))

from app.config import Settings, get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.ingest.indexer import index_version_for  # noqa: E402
from app.ingest.tokens import tokenizer_id  # noqa: E402
from app.providers import build_chat_model, build_embeddings, embed_query  # noqa: E402
from app.rag.answer import compose_answer  # noqa: E402
from app.rag.retriever import apply_relevance_floor, fit_context, search_chunks  # noqa: E402
from app.telemetry import Telemetry  # noqa: E402
from eval.corpus import load_questions  # noqa: E402
from eval.harness import build_world  # noqa: E402

RESULTS_DIR = pathlib.Path(__file__).resolve().parent / "results"


@dataclass
class QuestionResult:
    id: str
    kind: str
    lang: str
    expect: str
    hit: bool | None
    recall: float | None
    retrieved: int
    leaked: bool
    top_documents: list[str] = field(default_factory=list)
    # Answer-side, only with --answers.
    outcome: str | None = None
    citations: int | None = None
    citations_valid: bool | None = None
    grounded: bool | None = None
    behaviour_ok: bool | None = None
    notes: list[str] = field(default_factory=list)


def _contains(haystack: str, needle: str) -> bool:
    # Case-insensitive, whitespace-normalised: PDF and OCR text differ from the
    # source file in line breaks and double spaces, and a label should not fail for
    # that reason alone.
    return " ".join(needle.lower().split()) in " ".join(haystack.lower().split())


async def _retrieve(
    db: Database,
    settings: Settings,
    embeddings,
    claims: dict[str, str],
    *,
    query: str,
    product_id,
    mode: str,
    top_k: int,
    index_version: str,
):
    vector = None
    if mode in ("vector", "hybrid"):
        vector = await embed_query(embeddings, query)
    async with db.user_scope(claims) as conn:
        hits = await search_chunks(
            conn,
            query_embedding=vector,
            query_text=query,
            product_id=product_id,
            top_k=top_k,
            mode=mode,
            candidate_count=settings.retrieval_candidates,
            index_version=index_version,
        )
    return fit_context(
        apply_relevance_floor(hits, settings, mode=mode), settings.context_token_budget
    )


async def evaluate(
    *, mode: str, top_k: int, with_answers: bool, db: Database, settings: Settings, world, embeddings, telemetry
) -> tuple[list[QuestionResult], dict[str, Any]]:
    questions = load_questions()
    index_version = index_version_for(settings)
    chat = build_chat_model(settings) if with_answers else None
    results: list[QuestionResult] = []

    for question in questions:
        owner = world.owner if question["owner"] == "alice" else world.outsider
        product_id = owner.products.get(question.get("product") or "washer")
        evidence = question.get("evidence") or []
        forbidden = question.get("forbidden_evidence") or []

        chunks = await _retrieve(
            db,
            settings,
            embeddings,
            owner.claims,
            query=question["question"],
            product_id=product_id,
            mode=mode,
            top_k=top_k,
            index_version=index_version,
        )
        retrieved_text = "\n".join(chunk.content for chunk in chunks)

        found = [phrase for phrase in evidence if _contains(retrieved_text, phrase)]
        leaked = any(_contains(retrieved_text, phrase) for phrase in forbidden)

        result = QuestionResult(
            id=question["id"],
            kind=question["kind"],
            lang=question["lang"],
            expect=question["expect"],
            hit=bool(found) if evidence else None,
            recall=(len(found) / len(evidence)) if evidence else None,
            retrieved=len(chunks),
            leaked=leaked,
            top_documents=[chunk.document_title or "" for chunk in chunks[:3]],
        )
        if leaked:
            result.notes.append("LEAK: retrieved a phrase this user must not be able to see")

        if with_answers:
            answer = await compose_answer(
                chat_model=chat, question=question["question"], chunks=chunks
            )
            cited_ids = {citation["chunk_id"] for citation in answer.citations}
            retrieved_ids = {str(chunk.chunk_id) for chunk in chunks}

            result.outcome = answer.outcome
            result.citations = len(answer.citations)
            result.citations_valid = cited_ids <= retrieved_ids and not answer.dropped_labels

            cited_chunks = [chunk for chunk in chunks if str(chunk.chunk_id) in cited_ids]
            cited_text = "\n".join(chunk.content for chunk in cited_chunks)
            result.grounded = (
                any(_contains(cited_text, phrase) for phrase in evidence) if evidence else None
            )
            result.behaviour_ok = _behaviour_ok(question, answer, chunks)
            if answer.dropped_labels:
                result.notes.append(
                    f"model produced {len(answer.dropped_labels)} label(s) that were not in context"
                )
            if question["expect"] == "injection":
                bad = [
                    phrase
                    for phrase in question.get("forbidden_answer_phrases") or []
                    if _contains(answer.answer, phrase)
                ]
                if bad:
                    result.notes.append(f"INJECTION: answer echoed {bad}")

        results.append(result)

    return results, summarise(results, mode=mode, top_k=top_k, settings=settings, with_answers=with_answers)


def _behaviour_ok(question: dict, answer, chunks) -> bool:
    """Did the assistant do the right *kind* of thing for this question?"""
    expect = question["expect"]
    if expect == "answer":
        return answer.outcome in ("answered", "conflicting_sources") and bool(answer.citations)
    if expect == "no_answer":
        return answer.outcome == "no_answer"
    if expect == "conflict":
        # Presenting both sides counts even when the model labels it "answered", as
        # long as both conflicting sources are cited.
        return answer.outcome == "conflicting_sources" or len(answer.citations) >= 2
    if expect == "denied":
        return not chunks and answer.outcome == "no_answer"
    if expect == "injection":
        forbidden = question.get("forbidden_answer_phrases") or []
        return not any(_contains(answer.answer, phrase) for phrase in forbidden)
    return True


def summarise(
    results: list[QuestionResult], *, mode: str, top_k: int, settings: Settings, with_answers: bool
) -> dict[str, Any]:
    with_evidence = [r for r in results if r.recall is not None]
    answered = [r for r in results if r.outcome is not None]

    summary: dict[str, Any] = {
        "mode": mode,
        "top_k": top_k,
        "questions": len(results),
        "questions_with_labelled_evidence": len(with_evidence),
        # Reported separately from every average, because a leak is a failure rather
        # than a score to be averaged away.
        "leaks": sum(1 for r in results if r.leaked),
        "hit_at_k": _mean([1.0 if r.hit else 0.0 for r in with_evidence]),
        "recall_at_k": _mean([r.recall for r in with_evidence]),
        "mean_retrieved": _mean([float(r.retrieved) for r in results]),
        "by_kind": {},
    }

    kinds = sorted({r.kind for r in results})
    for kind in kinds:
        subset = [r for r in results if r.kind == kind and r.recall is not None]
        summary["by_kind"][kind] = {
            "questions": sum(1 for r in results if r.kind == kind),
            "hit_at_k": _mean([1.0 if r.hit else 0.0 for r in subset]),
            "recall_at_k": _mean([r.recall for r in subset]),
        }

    if with_answers and answered:
        summary["answers"] = {
            "evaluated": len(answered),
            "citations_valid": _mean([1.0 if r.citations_valid else 0.0 for r in answered]),
            "grounded": _mean(
                [1.0 if r.grounded else 0.0 for r in answered if r.grounded is not None]
            ),
            "behaviour_ok": _mean([1.0 if r.behaviour_ok else 0.0 for r in answered]),
            "abstained_when_expected": _mean(
                [1.0 if r.outcome == "no_answer" else 0.0 for r in answered if r.expect == "no_answer"]
            ),
            "answered_when_expected": _mean(
                [
                    1.0 if r.outcome in ("answered", "conflicting_sources") else 0.0
                    for r in answered
                    if r.expect == "answer"
                ]
            ),
        }

    # Two classes of metric, and mock mode can only speak to one of them.
    #
    #   mechanical — does the machinery do what it claims? Leakage, citation
    #     resolution, access scoping, and whether the three retrieval modes are
    #     genuinely different code paths. All of these are meaningful under the mock,
    #     which is why the mock run is worth having in CI.
    #
    #   judgement — does the model behave well? Abstaining when nothing supports an
    #     answer, presenting a conflict as a conflict, not echoing an instruction
    #     planted in a document, and retrieval recall on a paraphrase. None of these
    #     are meaningful under the mock: the stub answers from the top chunk whatever
    #     it says, and hash-based embeddings cannot match a paraphrase at all.
    summary["metrics"] = {
        "mechanical": ["leaks", "answers.citations_valid"],
        "judgement": [
            "hit_at_k",
            "recall_at_k",
            "answers.grounded",
            "answers.behaviour_ok",
            "answers.abstained_when_expected",
        ],
    }
    summary["quality_meaningful"] = not settings.is_mock
    if settings.is_mock:
        summary["caveat"] = (
            "PROVIDER_MODE=mock: hash-based bag-of-words embeddings and a scripted chat "
            "model. The mechanical metrics are real. The judgement metrics are not — the "
            "stub cannot abstain, cannot weigh two sources against each other, and cannot "
            "match a paraphrase, so low values there are properties of the stub."
        )
    return summary


def _mean(values: list[float]) -> float | None:
    values = [value for value in values if value is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def configuration(settings: Settings, embeddings) -> dict[str, Any]:
    """Everything a reader needs to know what produced these numbers."""
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider_mode": settings.provider_mode,
        "embedding_model": embeddings.model,
        "embedding_dimensions": embeddings.dimensions,
        "answer_model": settings.answer_model if not settings.is_mock else "mock/scripted",
        "tokenizer": tokenizer_id(),
        "index_version": index_version_for(settings),
        "chunk_target_tokens": settings.chunk_target_tokens,
        "chunk_overlap_tokens": settings.chunk_overlap_tokens,
        "tokens_per_chunk_note": (
            "Chunk sizes are in tokens as counted by the tokenizer named above. A different "
            "tokenizer produces different boundaries, which is why it is part of index_version."
        ),
        "retrieval_candidates": settings.retrieval_candidates,
        "retrieval_min_score": settings.retrieval_min_score,
        "retrieval_min_vector_similarity": settings.retrieval_min_vector_similarity,
        "context_token_budget": settings.context_token_budget,
    }


def render_markdown(config: dict[str, Any], runs: list[dict[str, Any]], details: dict[str, list[QuestionResult]]) -> str:
    lines = ["# Retrieval and answering evaluation", ""]
    if not config.get("quality_meaningful", True):
        lines += [
            "> **Mock providers.** `leaks` and `citations valid` are real results: they",
            "> measure the machinery, and the machinery is the real machinery. Everything",
            "> else on this page — hit@k, recall@k, grounded, behaviour ok, abstained —",
            "> is *not* a measurement of quality. The mock chat model answers from the top",
            "> retrieved chunk whatever it contains, so it cannot abstain and cannot",
            "> resist echoing text; the mock embeddings are hashed bag-of-words, so they",
            "> cannot match a paraphrase. Run with `PROVIDER_MODE=live` to measure quality.",
            "",
        ]
    lines += ["## Configuration", "", "| setting | value |", "| --- | --- |"]
    for key, value in config.items():
        if key == "quality_meaningful":
            continue
        lines.append(f"| {key} | `{value}` |")

    lines += ["", "## Retrieval", "", "| mode | top-k | hit@k | recall@k | mean chunks | leaks |", "| --- | --- | --- | --- | --- | --- |"]
    for run in runs:
        lines.append(
            f"| {run['mode']} | {run['top_k']} | {run['hit_at_k']} | {run['recall_at_k']} "
            f"| {run['mean_retrieved']} | {run['leaks']} |"
        )

    lines += ["", "### By question kind", "", "| kind | " + " | ".join(f"{r['mode']} hit@k" for r in runs) + " |"]
    lines.append("| --- | " + " | ".join("---" for _ in runs) + " |")
    for kind in sorted(runs[0]["by_kind"]):
        cells = [str(run["by_kind"].get(kind, {}).get("hit_at_k")) for run in runs]
        lines.append(f"| {kind} | " + " | ".join(cells) + " |")

    answer_runs = [run for run in runs if "answers" in run]
    if answer_runs:
        lines += [
            "",
            "## Answering",
            "",
            "| mode | citations valid | grounded | behaviour ok | abstained when expected | answered when expected |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for run in answer_runs:
            a = run["answers"]
            lines.append(
                f"| {run['mode']} | {a['citations_valid']} | {a['grounded']} | {a['behaviour_ok']} "
                f"| {a['abstained_when_expected']} | {a['answered_when_expected']} |"
            )

    for mode, rows in details.items():
        lines += ["", f"## Per question — {mode}", "", "| id | kind | expect | hit | recall | outcome | notes |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for row in rows:
            lines.append(
                f"| {row.id} | {row.kind} | {row.expect} | {row.hit} | {row.recall} "
                f"| {row.outcome or '—'} | {'; '.join(row.notes) or ''} |"
            )
    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="all", choices=["all", "vector", "lexical", "hybrid"])
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--answers", action="store_true", help="also run the answering step")
    parser.add_argument("--database", default=None, help="Postgres DSN to evaluate against")
    parser.add_argument("--keep", action="store_true", help="do not reset the evaluation database")
    parser.add_argument("--out", default=None, help="write results here instead of eval/results/")
    # Chunking is the setting most worth experimenting with, so it is a flag rather
    # than something to edit in .env between runs. Changing it changes index_version,
    # so the corpus is reindexed and the two runs never share a vector space.
    parser.add_argument("--chunk-target-tokens", type=int, default=None)
    parser.add_argument("--chunk-overlap-tokens", type=int, default=None)
    args = parser.parse_args()

    from devtools.localdb import create_database, reset_schema

    dsn = args.database or create_database("ai_vault_eval")
    if not args.keep:
        reset_schema(dsn)

    get_settings.cache_clear()
    settings = get_settings()
    overrides: dict[str, object] = {"database_url": dsn}
    if args.chunk_target_tokens is not None:
        overrides["chunk_target_tokens"] = args.chunk_target_tokens
    if args.chunk_overlap_tokens is not None:
        overrides["chunk_overlap_tokens"] = args.chunk_overlap_tokens
    settings = settings.model_copy(update=overrides)

    embeddings = build_embeddings(settings)
    top_k = args.top_k or settings.retrieval_top_k
    modes = ["vector", "lexical", "hybrid"] if args.mode == "all" else [args.mode]

    if settings.is_mock:
        print(
            "\n*** PROVIDER_MODE=mock ***\n"
            "Hash-based embeddings and a scripted chat model. The pipeline, the access\n"
            "scoping and the citation rules are exercised; retrieval and answer quality\n"
            "are NOT measured. Set PROVIDER_MODE=live with real keys for that.\n",
            file=sys.stderr,
        )

    db = Database(settings)
    await db.open()
    telemetry = Telemetry(None)  # evaluation runs do not pollute the operation log

    try:
        started = time.perf_counter()
        world = await build_world(db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)
        print(f"indexed {len(world.documents)} documents in {time.perf_counter() - started:.1f}s", file=sys.stderr)

        runs: list[dict[str, Any]] = []
        details: dict[str, list[QuestionResult]] = {}
        for mode in modes:
            results, summary = await evaluate(
                mode=mode,
                top_k=top_k,
                with_answers=args.answers,
                db=db,
                settings=settings,
                world=world,
                embeddings=embeddings,
                telemetry=telemetry,
            )
            runs.append(summary)
            details[mode] = results
            print(
                f"{mode:8s} hit@{top_k}={summary['hit_at_k']} recall@{top_k}={summary['recall_at_k']} "
                f"leaks={summary['leaks']}",
                file=sys.stderr,
            )
    finally:
        await db.close()

    config = configuration(settings, embeddings)
    config["quality_meaningful"] = not settings.is_mock

    out_dir = pathlib.Path(args.out) if args.out else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "mock" if settings.is_mock else "live"

    json_path = out_dir / f"{stamp}-{suffix}.json"
    json_path.write_text(
        json.dumps(
            {
                "configuration": config,
                "runs": runs,
                "questions": {mode: [vars(row) for row in rows] for mode, rows in details.items()},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    markdown_path = out_dir / f"{stamp}-{suffix}.md"
    markdown_path.write_text(render_markdown(config, runs, details), encoding="utf-8")

    print(f"\nwrote {json_path}\nwrote {markdown_path}")

    # A leak is the one result that fails the run.
    total_leaks = sum(run["leaks"] for run in runs)
    if total_leaks:
        print(f"FAILED: {total_leaks} cross-account retrieval leak(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
