# Document assistant service

A Python/FastAPI service next to the Expo app. It indexes the documents a user attaches
to a product, answers questions from those documents with citations, and prepares a
service-request draft that a person approves.

It does not replace anything. Receipt extraction stays in the Deno Edge Function, the
client stays Expo, and the database is the same Supabase Postgres — with pgvector and a
few more tables.

- [`docs/AI-ARCHITECTURE.md`](../../docs/AI-ARCHITECTURE.md) — how it fits together and why.
- [`docs/AI-DEMO.md`](../../docs/AI-DEMO.md) — a ten-minute walkthrough.
- [`docs/AI-INTERVIEW.md`](../../docs/AI-INTERVIEW.md) — what is actually implemented, where, and exercises.

## What you need

- Python 3.11+
- A Postgres 16 with **pgvector**. The local Supabase stack has it; a bare Postgres
  needs `apt-get install postgresql-16-pgvector` (or the equivalent).
- An OpenAI key (embeddings) and an Anthropic key (answering, and reading scans).
  Without them the service still starts and everything except search, indexing and
  answering works; those say so instead of failing silently.

## Setup

```bash
cd services/ai
python -m venv .venv
.venv/bin/pip install -r requirements.lock.txt   # exact, reproducible
cp .env.example .env                             # then fill it in
```

`requirements.txt` is the human-edited list of direct dependencies;
`requirements.lock.txt` is the resolved set the tests were verified against.

## Apply the migrations

The AI tables live in the project's normal Supabase migrations, so they go out the
normal way:

```bash
cd ../..                 # repo root
npx supabase start       # if it is not already running
npx supabase db reset    # applies every migration, including the AI ones
```

## Run it

```bash
cd services/ai
.venv/bin/python scripts/preflight.py                       # checks keys, models, schema
.venv/bin/python -m uvicorn app.main:app --port 8000 --reload
.venv/bin/python -m app.ingest.worker                       # second terminal
```

`GET /meta` reports the active configuration — models, index version, retrieval
settings, limits — and contains no secrets. It is the quickest way to confirm the app
and the service agree about what is running.

## Point the app at it

In the repo root `.env`:

```
EXPO_PUBLIC_AI_API_URL=http://127.0.0.1:8000
```

A simulator reaches the host on `127.0.0.1`. A physical device does not — use the
machine's LAN address (`http://192.168.x.y:8000`), and make sure the firewall allows it.
Restart the Expo dev server after changing `.env`; `EXPO_PUBLIC_*` values are inlined at
bundle time.

With no `EXPO_PUBLIC_AI_API_URL` set, the app works exactly as it did before and the new
screens explain what is missing.

## Tests

```bash
.venv/bin/python -m pytest
```

They run against a real local Postgres with pgvector, applying the project's real
migrations (see `devtools/localdb.py`). Providers are in explicit mock mode, so no keys
and no network are needed — and no test result is ever evidence about a model's quality.

```bash
# Uses TEST_ADMIN_DATABASE_URL, default postgresql://postgres@127.0.0.1:5432/postgres
TEST_ADMIN_DATABASE_URL=postgresql://postgres@127.0.0.1:54322/postgres .venv/bin/python -m pytest
```

## Evaluation

```bash
.venv/bin/python -m eval.run_eval --answers                   # all three retrieval modes
.venv/bin/python -m eval.run_eval --top-k 3 --chunk-target-tokens 110
```

Writes JSON and Markdown to `eval/results/`. In mock mode it labels every
judgement-dependent number as meaningless and says why; the mechanical ones (leakage,
citation validity) are real either way.

## The whole flow, printed

```bash
.venv/bin/python scripts/demo_walkthrough.py
```

Registers documents, indexes them, searches, asks, follows a citation, answers a
clarification, gets a draft, **restarts the process**, resumes, approves, and approves
again to show nothing is written twice. This is the dry run behind `docs/AI-DEMO.md`.

## Layout

```
app/
  config.py        settings, and the reasoning for each choice
  auth.py          Supabase token verification
  db.py            the two connection scopes — the whole authorization story
  storage.py       reading and deleting objects in the private bucket
  providers.py     embeddings and chat models behind LangChain interfaces
  mock_chat.py     scripted chat model for offline runs
  telemetry.py     what is logged, and what deliberately is not
  ingest/          extraction, chunking, indexing, the Postgres-queue worker
  rag/             prompts, retrieval, grounded answering and citation checks
  graph/           the LangGraph state machine, its tools, and draft persistence
  routers/         documents, search, chat, and a separate developer surface
devtools/          applies the project's real migrations to a local database
eval/              synthetic corpus, labelled questions, evaluation script
tests/             behaviour tests against real Postgres and real RLS
```
