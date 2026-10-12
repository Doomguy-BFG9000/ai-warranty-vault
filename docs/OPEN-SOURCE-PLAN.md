# Open-source readiness — AI Warranty Vault

## Context

The repo is going public as an engineering showcase. It already has the substance (Expo
app, Supabase with RLS, Deno edge functions, a Python RAG/LangGraph service with 141 tests
against real Postgres + pgvector), but not the packaging: no CI, a README written as dev
notes, personal paths in agent docs, and no CONTRIBUTING/SECURITY. Goal: a stranger lands
on the repo, understands it in 30 seconds, trusts it's safe, and can run it.

## Audit already done (read-only, before planning)

| Check | Result |
|---|---|
| Secrets in working tree **and full history** (`sk-ant-`, `sk-proj-`, `sk-…`, JWTs, AWS/GitHub/Slack tokens, private keys) | **Clean.** Only hits: the two Supabase local demo JWTs (`iss: supabase-demo`) in `scripts/verify-*.mjs`, and `sk-ant-not-a-real-key` in a test. |
| Hosted project ref (`*.supabase.co`) anywhere in history | None. The `1fcac42` types commit contains no URL/ref. |
| Real `.env` ever tracked | No. Only the three `.env.example` files. Deleted-file history: `.claude/settings.json` (just a plugin toggle), `App.tsx`, `index.ts`, `app/index.tsx`. |
| Personal data in fixtures | Eval corpus is synthetic (AquaWash 8000, BrewMaster 300); test users are `alice@/bob@example.test`. One cosmetic hit: a first-name address at `example.com` in `src/lib/__tests__/validation.test.ts`. |
| Personal paths in tracked files | `CLAUDE.md` and `AGENTS.md` end with a block describing the author's local, machine-specific session workflow (absolute home-directory paths). |
| TODO/FIXME/debug logging | None in app/service code. `console.log` only in the `scripts/verify-*.mjs` CLIs, where it *is* the output. |
| `TEST-*` files | Not in this checkout — they exist only on your Mac, never pushed. |
| Quality gates today | lint ✅, typecheck ✅, jest 70/70 ✅ (but noisy `[env] Missing EXPO_PUBLIC_…` warnings), pytest 141/141 ✅ (but `InsecureKeyLengthWarning` — test JWT secret < 32 bytes). |
| Commit authors (report only, nothing rewritten) | two identities: the maintainer's account (personal email) and a tooling identity. Reported to the maintainer; nothing rewritten. See "Needs from you". |

## Decisions (confirmed with you)

- Screenshots: I wire the README to `docs/screenshots/*.png`; **you** copy the chosen PNGs and push one commit. Raw `TEST-*` never enter git.
- `CLAUDE.md` / `AGENTS.md`: keep, strip only the machine-specific workflow block.
- Python CI: full suite on a `pgvector/pgvector:pg16` service container, `PROVIDER_MODE=mock`, no secrets.

## Tasks, in commit order — branch `chore/open-source-readiness` from `origin/main` (`1fcac42`)

1. **`docs: add the open-source readiness plan`** — `docs/OPEN-SOURCE-PLAN.md` (this file).
2. **`chore: ignore local test artifacts`** — `.gitignore`: `TEST-*`, `*.log`, `coverage/`, eval run outputs (confirm path under `services/ai/eval/` before adding).
3. **`chore: drop local-only workflow notes from agent docs`** — `CLAUDE.md`, `AGENTS.md`: remove the machine-specific workflow block and the pointer to a global config file outside the repo; keep every project rule.
4. **`docs(scripts): mark the embedded keys as Supabase's public local demo keys`** — one comment above each JWT in `scripts/verify-backend.mjs`, `verify-extract-fn.mjs`, `verify-reminders.mjs` (`iss: supabase-demo`, identical in every `supabase start`, only valid against localhost).
5. **`test: use neutral fake data`** — the first-name test address → `user@example.com`.
6. **`chore(env): document every variable in the env templates`** — `supabase/.env.example`: add optional `CLAUDE_MODEL`. `services/ai/.env.example`: add a commented "optional tuning" section for the 15 settings that have defaults but aren't listed (`ANSWER_MAX_TOKENS`, `RETRIEVAL_MIN_VECTOR_SIMILARITY`, `DB_STATEMENT_TIMEOUT_MS`, …) from `services/ai/app/config.py`. Root `.env.example` is already complete.
7. **`test: silence warnings the suites expect`** — add a jest `setupFiles` entry setting placeholder `EXPO_PUBLIC_SUPABASE_*` (warning comes from `src/lib/env.ts:26`); lengthen the pytest JWT secret in `services/ai/tests/conftest.py` (and the one-off in `test_auth.py`) to ≥ 32 bytes.
8. **`refactor: remove dead code`** — one-off checks, no new project deps: `tsc --noUnusedLocals --noUnusedParameters`, and `ruff check --select F401,F811,F841` from a scratch venv. Remove only what is provably unused; list anything ambiguous in the PR instead.
9. **`ci: lint, typecheck and test on every push and PR`** — `.github/workflows/ci.yml`:
   - `app` job: Node 22, `actions/setup-node` npm cache, `npm ci --legacy-peer-deps`, lint, typecheck, `npm test -- --ci`.
   - `ai` job: Python 3.11, pip cache keyed on `services/ai/requirements.lock.txt`, `pgvector/pgvector:pg16` service (trust auth), `TEST_ADMIN_DATABASE_URL=postgresql://postgres@localhost:5432/postgres` (read by `services/ai/devtools/localdb.py`), `pytest`.
   - `concurrency` cancels superseded runs; `permissions: contents: read`; no secrets referenced.
10. **`docs: rewrite the README as the project's front page`** — hero (name, pitch, CI + MIT badges, screenshot row), What it does (only shipped features: receipt capture → Claude vision extraction → editable review; vault with expiry; push + on-device reminders; freemium cap; document Q&A with page citations and abstention; human-approved claim drafts), Architecture (mermaid: Expo ↔ Supabase Auth/Postgres/Storage/Edge Functions ↔ `services/ai` FastAPI/LangGraph ↔ Claude + OpenAI embeddings; highlights: RLS-scoped hybrid search inside the top-k, LangGraph interrupts with exactly-once approval, server-verified citations, durable index jobs), Getting started (prereqs, three `.env.example`s, `supabase start`, AI service, Expo), Tech stack, Project structure, Testing (what each suite proves), Status ("MVP, actively evolving" + link to ROADMAP), License. Existing deep content moves to `docs/` links rather than being lost. Also fix README's "`expo lint`" (it's `eslint .`) and ROADMAP Phase 9's now-stale "no live-provider run behind it".
11. **`docs: add contributing and security policies`** — short `CONTRIBUTING.md` (setup, the four checks, conventional commits, the never-commit-secrets rule); `SECURITY.md` (report privately via GitHub's "Report a vulnerability"; scope; what's out of scope e.g. the demo keys).
12. **Fresh-clone verification**, then push and open the PR (summary, screenshots slot, suggested description + topics, verified checklist, manual to-dos).

## Risks

- **Typecheck in CI is weaker than locally**: `typedRoutes` types are generated only by the dev server, so on a clean runner route types are permissive. CI stays green honestly; I'll note it in the README rather than fake the generation.
- **`npm ci --legacy-peer-deps`** is needed for React 19 peer ranges (per `AGENTS.md`); if the lockfile and `--legacy-peer-deps` disagree on the runner I fix the lock in its own commit.
- **Cannot verify here**: `supabase start` (no Docker in this container) and the iOS simulator. Everything else in Getting started (install, lint, typecheck, jest, Python venv + pytest on pgvector, `ai:walkthrough` in mock mode) is verified from a fresh clone. The two unverified steps are marked in the PR for you to tick on your Mac.
- `extract-receipt` defaults to `claude-opus-5`; not changed here (out of scope), but flagged since `CLAUDE.md` says never assume a model id is still reachable.

## Needs from you

1. After I push: copy 4–6 PNGs to `docs/screenshots/` as `sign-in.png`, `vault.png`, `product-detail.png`, `ask-citation.png` (+ optional `review.png`, `claim-draft.png`), check they show no real email, and push.
2. **Commit authors (no rewrite will happen unless asked):** reported separately in the PR. Options: leave as is; set a GitHub noreply email for future commits (no rewrite); rewriting existing ones needs a force-push — a separate decision.
3. In GitHub settings when flipping public: set description + topics (from the PR), enable *Private vulnerability reporting* (SECURITY.md points to it), enable Actions, and optionally branch protection requiring CI.

## Verification

- Local: `npm run lint`, `npm run typecheck`, `npm test` (no warnings), `services/ai` `pytest` (no warnings).
- Fresh clone: `git clone` the pushed branch into a scratch dir and follow the README literally through every step that doesn't need Docker or a simulator.
- CI: workflow green on the PR for both jobs.
- Re-run the history secret scan on the final branch.

## Implementation notes

What changed against the plan while doing it:

- **CI found a real test fragility.** One test (the prompt-injection probe) passed locally
  and failed on GitHub's runner. Root cause: chunk sizes are measured with `tiktoken` when
  its vocabulary can be downloaded and with an approximate counter when it can't; this
  sandbox has no egress to that download, the runner does, so chunk boundaries differed
  and the test's question no longer reached the injected sentence. Reproduced locally by
  varying the chunk size (fails below ~60 tokens), fixed by making the question quote the
  injected sentence, and verified at every chunk size from 20 to 200. The assertion now
  reports what was retrieved and the pgvector version if it ever fails again.
- Action majors bumped to their Node 24 releases after the runner flagged Node 20 as
  deprecated.
- `docs/screenshots/README.md` lists the expected image names for the maintainer to fill.
