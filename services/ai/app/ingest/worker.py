"""The indexing worker.

A queue in Postgres, claimed with `FOR UPDATE SKIP LOCKED` and held on a lease.
That is the whole design, and it is deliberate: the alternative (Redis, Celery, a
broker) would add an operational component to a workload of a few documents per
user, and the database is already the thing that must agree with itself about what
is indexed.

What the lease buys: a worker that is killed mid-document leaves its job in
`running` with a `locked_at` that stops being refreshed. Once that timestamp is
older than the lease, any worker may reclaim the job. Recovery is therefore
automatic and needs no cleanup process — and because indexing is idempotent, a
reclaimed job that was actually almost finished simply redoes the work and
converges on the same rows.

Retries are bounded and only for transient failures. A provider timeout is worth
retrying; a PDF with no readable text is not, and retrying it would burn the
attempt budget on something a person has to fix by uploading a better scan.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from typing import Any

from app.config import Settings, get_settings
from app.db import Database, execute, fetch_one
from app.errors import ProviderError, UnprocessableDocument
from app.ingest.indexer import DocumentGone, index_document, index_version_for
from app.providers import EmbeddingHandle, build_embeddings
from app.telemetry import Telemetry, new_operation_id

logger = logging.getLogger("ai.worker")

_CLAIM_SQL = """
with candidate as (
  select id
  from public.document_index_jobs
  where (state = 'queued' and run_after <= now())
     -- Reclaim: a lease older than the timeout means whoever held it is gone.
     or (state = 'running' and locked_at < now() - make_interval(secs => %(lease)s))
  order by run_after, created_at
  for update skip locked
  limit 1
)
update public.document_index_jobs j
   set state     = 'running',
       locked_by = %(worker)s,
       locked_at = now(),
       attempts  = j.attempts + 1
  from candidate c
 where j.id = c.id
returning j.id, j.document_id, j.user_id, j.attempts, j.max_attempts
"""


async def claim_job(db: Database, settings: Settings) -> dict[str, Any] | None:
    async with db.service_scope() as conn:
        return await fetch_one(
            conn,
            _CLAIM_SQL,
            {"lease": settings.worker_lease_seconds, "worker": settings.worker_id},
        )


async def enqueue_job(db: Database, document_id: uuid.UUID, user_id: uuid.UUID, settings: Settings) -> bool:
    """Queue an indexing run. False when one is already live for this document.

    The uniqueness is a partial unique index, not a check-then-insert, so two
    simultaneous taps on "retry" cannot both win.
    """
    async with db.service_scope() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.document_index_jobs (document_id, user_id, index_version)
                values (%s, %s, %s)
                on conflict do nothing
                returning id
                """,
                (document_id, user_id, index_version_for(settings)),
            )
            return await cur.fetchone() is not None


async def _finish(db: Database, job_id: uuid.UUID, state: str, error: str | None = None) -> None:
    async with db.service_scope() as conn:
        await execute(
            conn,
            """
            update public.document_index_jobs
               set state = %s, locked_by = null, locked_at = null, last_error = %s
             where id = %s
            """,
            (state, error, job_id),
        )


async def _retry_later(db: Database, job_id: uuid.UUID, attempts: int, settings: Settings, error: str) -> None:
    # Exponential backoff from the attempt count, capped so a long outage does not
    # push a job a day into the future.
    delay = min(settings.worker_backoff_base_seconds * (2 ** max(0, attempts - 1)), 900)
    async with db.service_scope() as conn:
        await execute(
            conn,
            """
            update public.document_index_jobs
               set state     = 'queued',
                   locked_by = null,
                   locked_at = null,
                   last_error = %s,
                   run_after = now() + make_interval(secs => %s)
             where id = %s
            """,
            (error, delay, job_id),
        )
    logger.info("job %s re-queued in %ss after a transient failure", job_id, delay)


async def _fail_document(db: Database, document_id: uuid.UUID, code: str, message: str) -> None:
    async with db.service_scope() as conn:
        await execute(
            conn,
            """
            update public.product_documents
               set status = 'failed', error_code = %s, error_message = %s
             where id = %s
            """,
            (code, message, document_id),
        )


def _describe(exc: BaseException) -> tuple[str, str]:
    """Machine code and user-facing message for a failure."""
    if isinstance(exc, UnprocessableDocument):
        detail = exc.detail
        if isinstance(detail, dict):
            return str(detail.get("code", "unreadable_document")), str(detail.get("message", ""))
        return "unreadable_document", str(detail)
    if isinstance(exc, ProviderError):
        return exc.code, "The document could not be processed because an AI provider was unavailable."
    return type(exc).__name__, "Indexing failed unexpectedly. Please try again."


async def run_job(
    job: dict[str, Any],
    *,
    db: Database,
    settings: Settings,
    embeddings: EmbeddingHandle,
    telemetry: Telemetry,
) -> None:
    new_operation_id()
    job_id = job["id"]
    document_id = job["document_id"]

    try:
        outcome = await index_document(
            document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
        )
    except DocumentGone:
        # Lost the race with a delete. The job row is usually already cascaded
        # away; if it is not, close it out rather than retrying forever.
        logger.info("document %s disappeared mid-index; closing job", document_id)
        await _finish(db, job_id, "failed", "document deleted during indexing")
        return
    except UnprocessableDocument as exc:
        code, message = _describe(exc)
        # Terminal by nature: a better file is the only fix, so the attempt budget
        # is not spent on it. The user can still retry after re-uploading.
        await _fail_document(db, document_id, code, message)
        await _finish(db, job_id, "failed", code)
        return
    except ProviderError as exc:
        code, message = _describe(exc)
        if exc.retryable and job["attempts"] < job["max_attempts"]:
            await _retry_later(db, job_id, job["attempts"], settings, code)
            return
        await _fail_document(db, document_id, code, message)
        await _finish(db, job_id, "failed", code)
        return
    except Exception as exc:  # noqa: BLE001 - the worker must not die on one document
        logger.exception("unexpected failure indexing document %s", document_id)
        code, message = _describe(exc)
        if job["attempts"] < job["max_attempts"]:
            await _retry_later(db, job_id, job["attempts"], settings, code)
            return
        await _fail_document(db, document_id, code, message)
        await _finish(db, job_id, "failed", code)
        return

    await _finish(db, job_id, "succeeded", None)
    logger.info(
        "indexed document %s: %s chunks (%s)",
        document_id,
        outcome.chunk_count,
        "unchanged" if outcome.skipped else outcome.method,
    )


async def worker_loop(
    *,
    db: Database,
    settings: Settings,
    embeddings: EmbeddingHandle,
    telemetry: Telemetry,
    stop: asyncio.Event,
    max_jobs: int | None = None,
) -> int:
    """Claim and run jobs until `stop` is set.

    `max_jobs` exists for tests and for a one-shot "drain the queue" invocation;
    left as None the loop runs until asked to stop.
    """
    processed = 0
    # Bounds how many documents are embedded at once, which bounds both provider
    # concurrency and how much of a large PDF is held in memory.
    slots = asyncio.Semaphore(settings.worker_concurrency)
    running: set[asyncio.Task[None]] = set()

    async def _guarded(job: dict[str, Any]) -> None:
        async with slots:
            await run_job(job, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)

    while not stop.is_set():
        # Retire finished tasks first, and re-raise anything they failed with.
        # `run_job` handles every per-document failure itself, so an exception
        # surfacing here is a bug in the worker rather than a bad document, and it
        # should stop the loop loudly instead of being swallowed.
        if running:
            finished = {task for task in running if task.done()}
            running -= finished
            for task in finished:
                task.result()

        if max_jobs is not None and processed >= max_jobs:
            break

        if len(running) >= settings.worker_concurrency:
            done, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            continue

        job = await claim_job(db, settings)
        if job is None:
            # Bounded runs (tests, a one-shot drain) stop once the queue is empty
            # and nothing is still in flight.
            if max_jobs is not None and not running:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except asyncio.TimeoutError:
                pass
            continue

        processed += 1
        running.add(asyncio.create_task(_guarded(job)))

    if running:
        await asyncio.gather(*running)
    return processed


async def drain(db: Database, settings: Settings, telemetry: Telemetry, *, max_jobs: int = 50) -> int:
    """Run every job that is currently due, then return. Used by tests and CLI."""
    embeddings = build_embeddings(settings)
    stop = asyncio.Event()
    return await worker_loop(
        db=db, settings=settings, embeddings=embeddings, telemetry=telemetry, stop=stop, max_jobs=max_jobs
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = get_settings()
    db = Database(settings)
    await db.open()

    telemetry = Telemetry(db)
    embeddings = build_embeddings(settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Graceful stop: finish the documents in flight, claim nothing new. Any
        # job that does not finish is reclaimed by lease expiry.
        loop.add_signal_handler(sig, stop.set)

    logger.info(
        "worker %s started (index_version=%s, provider_mode=%s)",
        settings.worker_id,
        index_version_for(settings),
        settings.provider_mode,
    )
    try:
        await worker_loop(
            db=db, settings=settings, embeddings=embeddings, telemetry=telemetry, stop=stop
        )
    finally:
        await db.close()
        logger.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
