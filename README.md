# 🛡️ AI Warranty Vault

Mobile-first app that turns a **photo of a receipt** into tracked warranty coverage.
Snap a receipt → AI extracts the product, purchase date, price and warranty term →
the vault tracks the expiry and reminds you **before** coverage runs out.

- **Who it's for:** electronics owners, homeowners, and families.
- **Why it exists:** warranty proof is scattered across paper, email and boxes, so
  people miss claims. AI removes the manual data entry that kills every receipt organizer.
- **Business model:** freemium — free tier is limited by product count, premium unlocks
  unlimited storage.

## Tech stack

| Layer        | Choice                                                          |
| ------------ | -------------------------------------------------------------- |
| Client       | Expo / React Native (TypeScript, Expo Router)                  |
| Backend      | Supabase — Postgres + Auth + Storage + Edge Functions + pg_cron |
| Receipt AI   | Claude API (vision) — structured receipt extraction, in Deno    |
| Document AI  | `services/ai` — FastAPI + LangChain + LangGraph + pgvector       |
| Data fetching| TanStack Query                                                 |
| Notifications| expo-notifications + Expo Push                                 |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the phased build.

## Getting started

### 1. Install dependencies

```bash
npm install --legacy-peer-deps
```

### 2. Start the backend

The whole backend runs locally in Docker — no hosted project needed for development.

```bash
npm run db:start
```

This applies every migration in `supabase/migrations/` and prints your local API URL
and anon key. Supabase Studio is at http://127.0.0.1:54323.

### 3. Configure environment

```bash
cp .env.example .env
```

For local development use the values printed by `npm run db:start`:

```
EXPO_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
EXPO_PUBLIC_SUPABASE_ANON_KEY=<the ANON_KEY from db:start>
```

For a hosted project, take the URL and anon key from the Supabase dashboard →
Project Settings → API. The anon key is safe to ship — Row Level Security enforces
per-user access.

> **Server secrets** (`ANTHROPIC_API_KEY`, service-role key) are **never** in `.env`.
> They live only in the Supabase Edge Function environment. See `supabase/.env.example`.

### 4. Run the app

```bash
npm run ios      # iOS Simulator
npm run android  # Android emulator
npm start        # dev server + QR code for a physical device
```

## AI extraction

Receipt extraction runs in the `extract-receipt` Edge Function, never in the app —
that is what keeps the Anthropic key off the device.

```bash
cp supabase/.env.example supabase/.env   # add your ANTHROPIC_API_KEY
npm run fn:serve                          # serve functions against the local stack
```

For a hosted project, set the secret on the project instead:

```bash
npx supabase secrets set ANTHROPIC_API_KEY=sk-ant-... 
```

The model defaults to `claude-opus-5`. Extraction is a short, high-volume call, so
set `CLAUDE_MODEL` (e.g. `claude-sonnet-5`) if cost per receipt matters more than
accuracy — no code change required.

The function downloads the receipt **as the calling user**, so Storage RLS decides
what it may read. Verify that boundary — it needs no real API key:

```bash
npm run verify:extract-fn
```

## Document assistant

Attach a manual, warranty card or service terms to a product, then ask questions about
it. Answers are built **only** from the attached documents and cite the page they came
from; when the documents do not answer the question, the app says so rather than
guessing. It can also draft a service request, which a person edits and approves — the
app never sends anything.

This lives in [`services/ai`](services/ai) as a small Python service alongside the app.
It does not replace anything: the client stays Expo, the database stays the same
Supabase Postgres, and receipt extraction stays in the Deno Edge Function.

```bash
cd services/ai
python -m venv .venv && .venv/bin/pip install -r requirements.lock.txt
cp .env.example .env                       # keys and local Supabase values
.venv/bin/python scripts/preflight.py      # checks keys, models and schema

npm run ai:serve      # from the repo root, in one terminal
npm run ai:worker     # and the indexing worker in another
```

Then point the app at it in `.env`:

```
EXPO_PUBLIC_AI_API_URL=http://127.0.0.1:8000
```

With that unset the app works exactly as before and the new screens explain what is
missing.

- [`docs/AI-ARCHITECTURE.md`](docs/AI-ARCHITECTURE.md) — data flow, access boundaries,
  the graph, and the chunking/embedding/retrieval/checkpoint decisions.
- [`docs/AI-DEMO.md`](docs/AI-DEMO.md) — a ten-minute walkthrough with the documents and
  questions ready to paste.
- [`docs/AI-INTERVIEW.md`](docs/AI-INTERVIEW.md) — what is actually implemented, where it
  is, the trade-offs, and exercises to run yourself.

```bash
npm run ai:test         # behaviour tests against a real local Postgres + pgvector
npm run ai:eval         # retrieval and answering against a labelled question set
npm run ai:walkthrough  # the whole flow printed, including a restart mid-approval
```

## Verifying the backend

The security model (Row Level Security, storage isolation, the freemium cap) is
exercised against a real local stack rather than assumed:

```bash
npm run db:reset && npm run verify:backend
```

This signs up two users through the real API and asserts that neither can read,
write, or delete the other's data, that the free-tier cap returns HTTP 402, that a
client cannot grant itself premium, and that receipt images are not publicly
reachable. Run it after any change to `supabase/migrations/`.

## Project structure

```
app/            Expo Router routes (auth, tabs: vault / add / settings)
src/
  components/   Reusable UI
  hooks/        TanStack Query hooks
  lib/          supabase client, claude helpers, warranty/date utils
  theme/        Design tokens
  types/        Shared TS types (incl. the AI extraction schema)
supabase/
  migrations/   SQL schema + RLS + pg_cron + the AI tables and search functions
  functions/    Edge Functions: extract-receipt, send-reminders
services/ai/    Document assistant: FastAPI, LangChain, LangGraph, pgvector
  app/          service code (ingest, rag, graph, routers)
  eval/         synthetic corpus, labelled questions, evaluation script
  tests/        behaviour tests against real Postgres and real RLS
docs/           Product, architecture, roadmap, and the three AI docs
```

## Scripts

| Command                  | What it does                                        |
| ------------------------ | --------------------------------------------------- |
| `npm start`              | Start the Expo dev server                           |
| `npm run ios`            | Run on the iOS Simulator                            |
| `npm run android`        | Run on an Android emulator                          |
| `npm run db:start`       | Start the local Supabase stack (Docker)             |
| `npm run db:stop`        | Stop the local stack                                |
| `npm run db:reset`       | Re-apply all migrations from scratch                |
| `npm run db:types`       | Regenerate `src/types/database.ts` from the schema  |
| `npm run verify:backend` | Assert RLS, storage isolation and the freemium cap  |
| `npm run fn:serve`       | Serve Edge Functions against the local stack        |
| `npm run verify:extract-fn` | Assert the extraction function's security boundary |
| `npm run verify:reminders` | Assert reminder scheduling and once-only delivery |
| `npm test`               | Run unit tests                                      |
| `npm run typecheck`      | `tsc --noEmit`                                      |
| `npm run lint`           | `expo lint`                                         |
| `npm run ai:serve`       | Run the document-assistant API (services/ai)        |
| `npm run ai:worker`      | Run the document indexing worker                    |
| `npm run ai:preflight`   | Check AI keys, models and schema before demoing      |
| `npm run ai:test`        | Document-assistant tests (real Postgres + pgvector)  |
| `npm run ai:eval`        | Retrieval and answering evaluation                  |
| `npm run ai:walkthrough` | Print the whole document flow end to end            |

> After changing anything in `supabase/migrations/`, run `npm run db:reset && npm run db:types`
> so the generated types stay in sync with the schema.

## License

MIT — see [LICENSE](LICENSE).

## Reminders

Reminder rows are created by a database trigger whenever a product's expiry
changes — 30 and 7 days before it lapses. Delivery is two-pronged:

- **Server (primary):** an hourly `pg_cron` job calls the `send-reminders` Edge
  Function, which pushes through Expo and stamps `sent_at` so a window is never
  delivered twice. This works with the app closed.
- **On device (fallback):** the app schedules the same windows locally, so a user
  with no push token — or no connectivity when the job runs — still gets nudged.

For a hosted project the job needs two settings, which are per-project secrets
and so are not in the migration:

```bash
alter database postgres set app.settings.functions_url = 'https://<ref>.supabase.co/functions/v1';
alter database postgres set app.settings.service_role_key = '<service role key>';
```

Without them the job logs a notice and does nothing, rather than firing requests
that are guaranteed to fail.

```bash
npm run verify:reminders
```
