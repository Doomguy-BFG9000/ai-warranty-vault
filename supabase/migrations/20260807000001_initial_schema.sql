-- ─────────────────────────────────────────────────────────────────────────────
-- AI Warranty Vault — core schema
--
-- Three tables:
--   profiles  — one row per auth user, holds the freemium flag + push token
--   products  — a tracked purchase and its warranty window
--   reminders — scheduled nudges fired before a warranty expires
--
-- Row Level Security is added in a later migration; it is the primary
-- authorization boundary for this app.
-- ─────────────────────────────────────────────────────────────────────────────

-- Needed for gen_random_uuid() on older Postgres; no-op when already present.
create extension if not exists "pgcrypto";


-- ── profiles ────────────────────────────────────────────────────────────────
create table public.profiles (
  id              uuid primary key references auth.users (id) on delete cascade,
  email           text,
  is_premium      boolean     not null default false,
  expo_push_token text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

comment on table public.profiles is
  'Per-user profile. is_premium gates the freemium product limit.';
comment on column public.profiles.expo_push_token is
  'Expo push token used by the send-reminders Edge Function.';


-- ── products ────────────────────────────────────────────────────────────────
create type public.extraction_status as enum ('pending', 'done', 'failed');

create table public.products (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references auth.users (id) on delete cascade,

  name                text not null,
  merchant            text,
  category            text,

  price_cents         integer,
  currency            text not null default 'USD',

  purchase_date       date,
  warranty_months     integer,

  -- Derived from purchase_date + warranty_months so the expiry can never drift
  -- out of sync with its inputs. A user who needs a different end date adjusts
  -- warranty_months (kept simple deliberately for the MVP).
  warranty_expires_at date generated always as (
    case
      when purchase_date is null or warranty_months is null then null
      else (purchase_date + make_interval(months => warranty_months))::date
    end
  ) stored,

  receipt_image_path  text,
  extraction_status   public.extraction_status not null default 'pending',
  notes               text,

  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),

  constraint products_name_not_blank      check (length(btrim(name)) > 0),
  constraint products_price_non_negative  check (price_cents is null or price_cents >= 0),
  constraint products_warranty_months_sane
    check (warranty_months is null or (warranty_months >= 0 and warranty_months <= 600)),
  constraint products_currency_iso4217    check (currency ~ '^[A-Z]{3}$')
);

comment on table public.products is
  'A purchase whose warranty the user is tracking.';
comment on column public.products.warranty_expires_at is
  'Generated: purchase_date + warranty_months. Null until both are known.';
comment on column public.products.receipt_image_path is
  'Object path in the private "receipts" storage bucket: {user_id}/{uuid}.jpg';

create index products_user_id_idx on public.products (user_id);
create index products_user_expiry_idx on public.products (user_id, warranty_expires_at);


-- ── reminders ───────────────────────────────────────────────────────────────
create table public.reminders (
  id         uuid primary key default gen_random_uuid(),
  product_id uuid not null references public.products (id) on delete cascade,
  user_id    uuid not null references auth.users (id) on delete cascade,
  remind_at  timestamptz not null,
  channel    text not null default 'push',
  sent_at    timestamptz,
  created_at timestamptz not null default now(),

  constraint reminders_channel_supported check (channel in ('push'))
);

comment on table public.reminders is
  'Scheduled pre-expiry nudges. Rows are kept in sync by sync_product_reminders().';

-- The daily cron scan reads exactly this slice: due and not yet sent.
create index reminders_due_idx on public.reminders (remind_at) where sent_at is null;
create index reminders_product_id_idx on public.reminders (product_id);


-- ── updated_at maintenance ──────────────────────────────────────────────────
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

create trigger products_set_updated_at
  before update on public.products
  for each row execute function public.set_updated_at();
