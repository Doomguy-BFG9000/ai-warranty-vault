-- ─────────────────────────────────────────────────────────────────────────────
-- Business rules enforced in the database
--
--   1. Every new auth user gets a profile row.
--   2. Free-tier users are capped at FREE_TIER_PRODUCT_LIMIT products.
--   3. Reminder rows stay in sync with a product's warranty expiry.
--
-- These live in the DB rather than the client so they cannot be bypassed by a
-- modified app talking straight to the API with the anon key.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. Profile bootstrap on signup ──────────────────────────────────────────
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();


-- ── 2. Freemium product cap ─────────────────────────────────────────────────
-- Single source of truth for the limit, so the client can read the same number
-- instead of hardcoding it in two places.
create or replace function public.free_tier_product_limit()
returns integer
language sql
immutable
as $$ select 5 $$;

comment on function public.free_tier_product_limit() is
  'Max products a non-premium user may track. Read by the app to show the upgrade sheet.';

-- The client calls this to render the upgrade sheet, so it needs execute.
grant execute on function public.free_tier_product_limit() to authenticated;

create or replace function public.enforce_free_tier_limit()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_is_premium boolean;
  v_count      integer;
  v_limit      integer := public.free_tier_product_limit();
begin
  -- A BEFORE INSERT trigger runs ahead of the RLS WITH CHECK, so an attempt to
  -- insert a row owned by someone else would otherwise be answered with *that*
  -- user's quota state — leaking whether a stranger has hit their limit. Defer
  -- to RLS for those rows: it rejects them with the correct error and tells the
  -- caller nothing about the other account.
  --
  -- The test is on auth.uid() rather than current_user because this function is
  -- SECURITY DEFINER (it needs an RLS-independent count), and inside a definer
  -- function current_user is the owner, not the caller. auth.uid() reads the
  -- request's JWT claims and is null for service-role and direct SQL callers,
  -- for whom the cap is enforced normally.
  if (select auth.uid()) is not null
     and new.user_id is distinct from (select auth.uid()) then
    return new;
  end if;

  select is_premium into v_is_premium
  from public.profiles
  where id = new.user_id;

  if coalesce(v_is_premium, false) then
    return new;
  end if;

  select count(*) into v_count
  from public.products
  where user_id = new.user_id;

  if v_count >= v_limit then
    -- PT402 → PostgREST maps PTxyz to HTTP status xyz, so the client receives a
    -- 402 Payment Required and can show the upgrade sheet. The message prefix is
    -- also matched client-side so behaviour survives any mapping change.
    raise exception
      'FREE_TIER_LIMIT_REACHED: free plan is limited to % products. Upgrade for unlimited storage.',
      v_limit
      using errcode = 'PT402';
  end if;

  return new;
end;
$$;

create trigger products_enforce_free_tier
  before insert on public.products
  for each row execute function public.enforce_free_tier_limit();


-- ── 3. Keep reminders in sync with warranty expiry ──────────────────────────
-- Fires 30 and 7 days before expiry. Regenerating on every change keeps the
-- schedule correct when a user edits the purchase date or warranty length on
-- the review screen.
create or replace function public.sync_product_reminders()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_offset_days integer;
begin
  -- Drop pending reminders; already-sent ones are history and stay untouched.
  delete from public.reminders
  where product_id = new.id
    and sent_at is null;

  if new.warranty_expires_at is null then
    return new;
  end if;

  foreach v_offset_days in array array[30, 7] loop
    declare
      v_remind_at timestamptz :=
        (new.warranty_expires_at - make_interval(days => v_offset_days))::timestamptz
        + interval '9 hours';  -- late-morning local-ish, not the middle of the night
    begin
      -- Skip windows already in the past (e.g. adding an old receipt).
      if v_remind_at > now() then
        insert into public.reminders (product_id, user_id, remind_at, channel)
        values (new.id, new.user_id, v_remind_at, 'push');
      end if;
    end;
  end loop;

  return new;
end;
$$;

create trigger products_sync_reminders
  after insert on public.products
  for each row execute function public.sync_product_reminders();

create trigger products_sync_reminders_on_change
  after update of purchase_date, warranty_months on public.products
  for each row execute function public.sync_product_reminders();
