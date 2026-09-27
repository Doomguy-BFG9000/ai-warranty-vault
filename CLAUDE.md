# CLAUDE.md — AI Warranty Vault

Project-specific instructions. Inherits the global engineering/product principles from
`~/Personal/.claude/CLAUDE.md` (MVP speed, simple maintainable code, conventional commits,
never commit secrets).

@AGENTS.md

## What this is

Mobile-first app: photograph a receipt → **Claude vision** extracts product + warranty →
the vault tracks expiry → reminders fire before coverage ends. Freemium (free tier limited
by product count; `is_premium` unlocks unlimited). Full context in
[`docs/PRODUCT.md`](docs/PRODUCT.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Stack

- **Client:** Expo SDK 57 / React Native, TypeScript, **Expo Router** (file-based routes).
- **Backend:** Supabase — Postgres (RLS + pgvector), Auth, Storage, Edge Functions (Deno), pg_cron.
- **Receipt AI:** Claude API vision in the `extract-receipt` Edge Function (Deno).
- **Document AI:** `services/ai` — Python 3.11, FastAPI, LangChain, LangGraph,
  pgvector. Indexes product documents, answers questions from them with citations, and
  prepares human-approved claim drafts. See [`docs/AI-ARCHITECTURE.md`](docs/AI-ARCHITECTURE.md).
- **Data:** TanStack Query over the Supabase JS client.
- **Notifications:** expo-notifications + Expo Push.

## Conventions

- Import alias: `@/` → `src/`. UI in `src/components`, query hooks in `src/hooks`,
  clients/utilities in `src/lib`, shared types in `src/types`, design tokens in `src/theme`.
- Use the design tokens in `src/theme` — no hardcoded colors/spacing in components.
- Screens live in `app/` (Expo Router). Keep screen files thin; push logic into hooks/lib.
- All data access goes through TanStack Query hooks, not raw calls inside components.
- Calls to the document-assistant service go through `src/lib/aiApi.ts`, never `fetch`
  in a component. Its types mirror `services/ai/app/schemas.py` in `src/types/ai.ts`.

### Python service (`services/ai`)

- Request work runs under Row Level Security as `authenticated`; the privileged scope is
  for the indexing worker and server-authored rows only. `app/db.py` explains the
  boundary and `tests/test_access_isolation.py` enforces it.
- Model names are always environment variables. Never hardcode one, and never assume an
  identifier already in the code is still reachable — `scripts/preflight.py` checks.
- `PROVIDER_MODE=mock` is for offline tests and never engages as a fallback: a provider
  failure surfaces as an error rather than degrading to fake embeddings.
- Changing chunking or the embedding model changes `index_version`, which excludes older
  chunks from search until documents are reindexed. That is intentional — do not work
  around it.
- Pinned dependencies. `requirements.txt` is the direct list; `requirements.lock.txt` is
  the resolved set the tests passed against. Regenerate the lock only from a green run.

## Secrets — hard rules

- The app bundle may only contain `EXPO_PUBLIC_*` values (Supabase URL + anon key). The
  anon key is safe because **Row Level Security** enforces per-user access.
- `ANTHROPIC_API_KEY` and the Supabase **service-role** key live ONLY in the Edge Function
  environment (`supabase secrets set`). Never in `.env`, the client, or git.
- `.env` is git-ignored; `.env.example` is the tracked template. Never commit real keys.

## AI extraction principle

Claude output is an **assistant, not the authority**. Extracted fields are always shown on
an **editable review screen** and only persisted after the user confirms.

## Working style

- Plan before coding; explain non-obvious decisions.
- Prefer simple, tested, maintainable code over cleverness. Avoid premature optimization
  and unnecessary dependencies.
- Conventional commits (`feat:`, `fix:`, `chore:`, `docs:` …), one coherent change each.
- Commit messages describe the change only. Never add a `Co-Authored-By: Claude`
  trailer or any other mention of an LLM — these repositories are public-facing
  professional work.

## Commands

```bash
npm run ios         # run on iOS Simulator
npm start           # dev server
npm run typecheck   # tsc --noEmit
npm run lint        # expo lint
npm test            # jest

# document assistant (services/ai)
npm run ai:serve        # FastAPI on :8000
npm run ai:worker       # indexing worker
npm run ai:test         # pytest against a real local Postgres + pgvector
npm run ai:preflight    # verify keys, models and schema
npm run ai:eval         # retrieval + answering evaluation
npm run ai:walkthrough  # the whole document flow, printed
```

<!-- BEGIN: personal-status -->
# ~/Personal STATUS.md

- This repository lives under `/Users/greg/Personal`.
- Session start: open `/Users/greg/Personal/STATUS.md`, find this folder, continue from **Next**. Do not rediscover the project from README if Next is clear.
- Session end (even if not asked): rewrite `/Users/greg/Personal/STATUS.md` — update only this project's block and the top indexes; never delete other projects; never commit STATUS.md here.
- Protocol: `/Users/greg/Personal/AGENTS.md`
<!-- END: personal-status -->
