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
| AI           | Claude API (vision) — structured receipt extraction            |
| Data fetching| TanStack Query                                                 |
| Notifications| expo-notifications + Expo Push                                 |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the phased build.

## Getting started

### 1. Install dependencies

```bash
npm install --legacy-peer-deps
```

### 2. Configure environment

```bash
cp .env.example .env
```

Fill in your Supabase project URL and anon key (from the Supabase dashboard →
Project Settings → API). The anon key is safe to ship — Row Level Security enforces
per-user access.

> **Server secrets** (`ANTHROPIC_API_KEY`, service-role key) are **never** in `.env`.
> They live only in the Supabase Edge Function environment. See `supabase/.env.example`.

### 3. Run the app

```bash
npm run ios      # iOS Simulator
npm run android  # Android emulator
npm start        # dev server + QR code for a physical device
```

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
  migrations/   SQL schema + RLS + pg_cron
  functions/    Edge Functions: extract-receipt, send-reminders
docs/           Product, architecture and roadmap docs
```

## Scripts

| Command             | What it does                     |
| ------------------- | -------------------------------- |
| `npm start`         | Start the Expo dev server        |
| `npm run ios`       | Run on the iOS Simulator         |
| `npm run android`   | Run on an Android emulator       |
| `npm run typecheck` | `tsc --noEmit`                   |
| `npm run lint`      | `expo lint`                      |

## License

MIT — see [LICENSE](LICENSE).
