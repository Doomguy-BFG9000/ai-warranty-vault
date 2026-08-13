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
- **Backend:** Supabase — Postgres (RLS), Auth, Storage, Edge Functions (Deno), pg_cron.
- **AI:** Claude API vision in the `extract-receipt` Edge Function.
- **Data:** TanStack Query over the Supabase JS client.
- **Notifications:** expo-notifications + Expo Push.

## Conventions

- Import alias: `@/` → `src/`. UI in `src/components`, query hooks in `src/hooks`,
  clients/utilities in `src/lib`, shared types in `src/types`, design tokens in `src/theme`.
- Use the design tokens in `src/theme` — no hardcoded colors/spacing in components.
- Screens live in `app/` (Expo Router). Keep screen files thin; push logic into hooks/lib.
- All data access goes through TanStack Query hooks, not raw calls inside components.

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
```
