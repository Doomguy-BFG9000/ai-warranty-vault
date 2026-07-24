# Architecture — AI Warranty Vault

## System overview

```
┌─────────────────────────────┐         ┌──────────────────────────────────┐
│  Expo App (iOS / Android)   │         │  Supabase                        │
│  - Expo Router (tabs)       │  HTTPS  │  - Auth (email / password)       │
│  - expo-camera / picker     │◄───────►│  - Postgres (Row Level Security) │
│  - expo-notifications       │         │  - Storage bucket: receipts      │
│  - TanStack Query           │         │  - Edge Functions (Deno):        │
│  - Supabase JS client       │         │      • extract-receipt (Claude)  │
└─────────────────────────────┘         │      • send-reminders (cron)     │
                                         │  - pg_cron → daily reminder scan │
                                         └───────────────┬──────────────────┘
                                                         │ Claude API (vision)
                                                         ▼
```

## Client

- **Expo Router** file-based routing. Route groups: `(auth)` for signed-out screens,
  `(tabs)` for the authenticated app (Vault / Add / Settings).
- **Session storage:** Supabase session persisted with `expo-secure-store` (encrypted),
  falling back to AsyncStorage on web.
- **Data layer:** TanStack Query wraps all Supabase reads/writes for caching, loading and
  error states.
- **State kept minimal:** server is the source of truth; little global client state.

## Data model (Postgres)

All tables have Row Level Security so a user can only ever see their own rows
(`user_id = auth.uid()`).

### `profiles`
| column          | type        | notes                                   |
| --------------- | ----------- | --------------------------------------- |
| id              | uuid PK     | equals `auth.users.id`                  |
| email           | text        |                                         |
| is_premium      | boolean     | default `false` — freemium gate         |
| expo_push_token | text null   | for push notifications                  |
| created_at      | timestamptz | default `now()`                         |

### `products`
| column               | type        | notes                                     |
| -------------------- | ----------- | ----------------------------------------- |
| id                   | uuid PK     |                                           |
| user_id              | uuid FK     | → auth.users                              |
| name                 | text        | product name                              |
| merchant             | text null   |                                           |
| category             | text null   | electronics / appliance / …               |
| price_cents          | integer null|                                           |
| currency             | text        | ISO 4217, default derived                 |
| purchase_date        | date null   |                                           |
| warranty_months      | integer null|                                           |
| warranty_expires_at  | date null   | computed = purchase_date + warranty_months|
| receipt_image_path   | text null   | Storage object path (private bucket)      |
| extraction_status    | text        | pending / done / failed                   |
| notes                | text null   |                                           |
| created_at/updated_at| timestamptz |                                           |

### `reminders`
| column      | type        | notes                                    |
| ----------- | ----------- | ---------------------------------------- |
| id          | uuid PK     |                                          |
| product_id  | uuid FK     | → products (cascade delete)              |
| user_id     | uuid FK     | → auth.users                             |
| remind_at   | timestamptz | when to fire                             |
| channel     | text        | `push` (MVP)                             |
| sent_at     | timestamptz null | stamped once delivered              |

## Key flows

### Receipt extraction
1. Client captures/picks an image, downsizes it (`expo-image-manipulator`).
2. Uploads to the **private** `receipts` Storage bucket at `{user_id}/{uuid}.jpg`.
3. Inserts a `products` row with `extraction_status = 'pending'`.
4. Calls the **`extract-receipt`** Edge Function with the storage path.
5. The function downloads the image (service role), sends it to **Claude vision** with a
   strict JSON-schema prompt, and returns structured fields.
6. Client shows an **editable review screen** — the user confirms/corrects, then saves.
   `warranty_expires_at` is computed and reminder rows are created.

> The AI is an assistant, never the authority: nothing is persisted as final until the
> user confirms it on the review screen.

### Reminders
- On save, insert `reminders` at e.g. 30 days and 7 days before `warranty_expires_at`.
- **`send-reminders`** Edge Function runs daily via **pg_cron**: finds due, unsent
  reminders → sends via the **Expo Push API** → stamps `sent_at`.
- The client also schedules a **local notification** as an offline fallback.

### Freemium gating
- Free users are capped at 5 products. Enforced server-side (DB trigger / function check
  on insert) so it can't be bypassed by the client; `is_premium = true` removes the cap.
- The client reads the count and shows an upgrade sheet at the limit.

## Security & secrets

- **App bundle** only ever holds `EXPO_PUBLIC_SUPABASE_URL` + anon key (safe under RLS).
- **`ANTHROPIC_API_KEY`** and the **service-role key** live only in the Edge Function
  environment (`supabase secrets set`), never in the client or git.
- Receipt images are in a **private** bucket; access via short-lived signed URLs / the
  service role inside functions.
- RLS is the primary authorization boundary and is covered by verification tests.

## Why these choices

- **Expo** → one TS codebase, native camera, and reliable push (core to reminders).
- **Supabase** → Postgres + Auth + Storage + scheduled functions in one, the fastest path
  to a validated MVP without standing up bespoke infra.
- **Claude vision** → strong structured extraction directly from a photo, no separate OCR
  step to maintain.
