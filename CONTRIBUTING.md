# Contributing

Thanks for taking a look. This is a personal showcase project, but issues and small,
focused pull requests are welcome.

## Setup

Follow [Getting started](README.md#getting-started). For the document assistant tests
you also need a local Postgres 16 with pgvector (see [Testing](README.md#testing)).

## Before opening a pull request

Run the same checks CI runs:

```bash
npm run lint
npm run typecheck
npm test
npm run ai:test        # only if you touched services/ai or supabase/migrations
```

If you change `supabase/migrations/`, add a **new** migration rather than editing an
applied one, and regenerate the client types with `npm run db:types`.

## Conventions

- **Conventional commits** — `feat:`, `fix:`, `docs:`, `test:`, `chore:` … one coherent
  change per commit.
- **TypeScript:** import via the `@/` alias; UI in `src/components`, data access only
  through TanStack Query hooks in `src/hooks`; use the design tokens in `src/theme`
  instead of hard-coded colours or spacing; keep screens in `app/` thin.
- **Python (`services/ai`):** request work runs under Row Level Security as the calling
  user; model names come from environment variables, never from code; pinned
  dependencies — `requirements.lock.txt` is regenerated only from a green test run.
- **AI output is a suggestion.** Anything a model extracts is shown for review before
  it is saved.

## Never commit secrets

Only `EXPO_PUBLIC_*` values may reach the app bundle. API keys and the Supabase
service-role key belong in git-ignored `.env` files or `supabase secrets` — the tracked
`.env.example` files show what is needed with placeholders. If you spot a secret in the
repository, please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
