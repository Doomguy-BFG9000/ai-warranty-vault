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
cp .env.example .env
```

`requirements.txt` is the human-edited list of direct dependencies;
`requirements.lock.txt` is the resolved set the tests were verified against.

`.env.example` documents every setting, but almost all of them have working defaults in
`app/config.py` — a real `.env` needs only the values that have none:

```bash
DATABASE_URL=                 # local stack: already correct by default
SUPABASE_URL=                 # local stack: already correct by default
SUPABASE_SERVICE_ROLE_KEY=    # `npx supabase start` prints it
SUPABASE_JWT_SECRET=          # `npx supabase status` prints it (see note below)
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

Against a hosted project all six come from the dashboard; the header of
`.env.example` says which page each one is on. `SUPABASE_JWT_SECRET` is only needed
when the project shows a symmetric JWT secret — newer projects use asymmetric signing
keys, and leaving it unset makes the service verify tokens against the project's JWKS
endpoint instead.

## Apply the migrations

The AI tables live in the project's normal Supabase migrations, so they go out the
normal way.

**Local stack** (needs Docker running):

```bash
cd ../..                 # repo root
npx supabase start       # if it is not already running
npx supabase db reset    # applies every migration, including the AI ones
```

**Hosted project** (no Docker needed):

```bash
cd ../..
npx supabase login
npx supabase link --project-ref <ref>     # the ref is in the dashboard URL
npx supabase db push                      # NOT `db reset` — that would wipe the project
npx supabase gen types typescript --linked > src/types/database.ts
npx supabase migration list               # confirm the 20260927* and 20260929* rows landed
```

If `db push` cannot create the `vector` extension, enable it in
Dashboard → Database → Extensions and push again.

> **Never point the test suite or the evaluation at a hosted project.** Both reset the
> `public`, `auth` and `storage` schemas by design, which on a real project means
> deleting its data. They need a local Postgres — either the Supabase stack, or
> `brew install postgresql@16 pgvector` — and `TEST_ADMIN_DATABASE_URL` pointed at it.

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
