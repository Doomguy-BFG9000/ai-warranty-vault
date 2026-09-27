-- ─────────────────────────────────────────────────────────────────────────────
-- Minimal Supabase-compatible shim for a bare Postgres instance.
--
-- The tests run the project's real migrations — the same SQL that ships to
-- Supabase — against a plain local Postgres 16 with pgvector. That only works if
-- the handful of things Supabase provides out of the box exist first: the
-- `anon` / `authenticated` / `service_role` roles, `auth.uid()`, an `auth.users`
-- table for the foreign keys, and enough of `storage` for the bucket policies.
--
-- This file is deliberately *not* a Supabase emulator. It provides exactly the
-- surface the migrations touch, and it provides the real thing where it matters:
-- `auth.uid()` reads `request.jwt.claims` exactly as Supabase's does, so RLS
-- behaves in tests the way it behaves in production. Anything the shim gets
-- wrong would show up as a test that passes here and fails against
-- `supabase db reset`, which is why the same migrations are also runnable there.
-- ─────────────────────────────────────────────────────────────────────────────

create extension if not exists pgcrypto;

-- ── roles ───────────────────────────────────────────────────────────────────
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end;
$$;


-- ── auth ────────────────────────────────────────────────────────────────────
create schema if not exists auth;

-- Only the columns the project's migrations and triggers reference.
create table if not exists auth.users (
  id         uuid primary key default gen_random_uuid(),
  email      text,
  created_at timestamptz not null default now()
);

-- The real definition, copied in behaviour: the uid comes from the request's JWT
-- claims, which PostgREST (and this project's API) set with
-- `set local request.jwt.claims = ...`. Returns null when unset, which is what
-- makes service-role and direct-SQL callers distinguishable.
--
-- The `nullif` is applied to the *setting*, before the jsonb cast, and that order
-- matters: a custom GUC that was once set transaction-locally reverts to the empty
-- string rather than to unset, and `''::jsonb` is an error. Supabase's own
-- definition guards it the same way.
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
  select nullif(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub',
    ''
  )::uuid;
$$;

create or replace function auth.jwt()
returns jsonb
language sql
stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb,
    '{}'::jsonb
  );
$$;

create or replace function auth.role()
returns text
language sql
stable
as $$
  select nullif(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role',
    ''
  );
$$;

grant usage on schema auth to anon, authenticated, service_role;
grant select on auth.users to service_role;


-- ── storage ─────────────────────────────────────────────────────────────────
-- Enough of the Storage schema for the bucket rows and the object policies to be
-- created and exercised. Object bytes are not modelled: the tests that need file
-- content feed the extractor directly.
create schema if not exists storage;

create table if not exists storage.buckets (
  id                 text primary key,
  name               text not null,
  public             boolean not null default false,
  file_size_limit    bigint,
  allowed_mime_types text[],
  created_at         timestamptz not null default now()
);

create table if not exists storage.objects (
  id          uuid primary key default gen_random_uuid(),
  bucket_id   text not null references storage.buckets (id) on delete cascade,
  name        text not null,
  owner       uuid,
  metadata    jsonb,
  created_at  timestamptz not null default now()
);

alter table storage.objects enable row level security;

-- Splits an object path into its segments, as Supabase's helper does.
create or replace function storage.foldername(name text)
returns text[]
language plpgsql
immutable
as $$
declare
  parts text[];
begin
  parts := string_to_array(name, '/');
  return parts[1 : array_length(parts, 1) - 1];
end;
$$;

grant usage on schema storage to anon, authenticated, service_role;
grant select, insert, update, delete on storage.objects to authenticated, service_role;
grant select on storage.buckets to authenticated, service_role;


-- ── extensions schema ───────────────────────────────────────────────────────
-- The migrations install pgvector into `extensions`, matching Supabase. Grant
-- usage so `authenticated` can name the vector type and its operators.
create schema if not exists extensions;
grant usage on schema extensions to anon, authenticated, service_role;
