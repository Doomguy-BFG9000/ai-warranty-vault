-- ─────────────────────────────────────────────────────────────────────────────
-- Row Level Security
--
-- RLS is the authorization boundary for this app: the mobile client ships with
-- the anon key, so these policies are what actually keep one user's receipts
-- away from another's. Every table is deny-by-default, then narrowly opened to
-- rows owned by auth.uid().
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Table privileges ────────────────────────────────────────────────────────
-- RLS only ever *narrows* access; it does not grant it. Without these grants
-- every client query fails with "permission denied for table ...". Grants are
-- explicit here rather than relying on default privileges so the schema is
-- self-contained and behaves identically on a fresh project.
--
-- `anon` is deliberately given nothing: all data access happens after login,
-- and authentication itself goes through GoTrue rather than PostgREST.

grant usage on schema public to authenticated, service_role;

grant select, update                 on public.profiles  to authenticated;
grant select, insert, update, delete on public.products  to authenticated;
grant select, delete                 on public.reminders to authenticated;

-- Edge Functions run as service_role: extract-receipt writes back extraction
-- results, send-reminders reads due reminders and stamps sent_at, and billing
-- updates is_premium. service_role bypasses RLS but still needs table
-- privileges, and without these every server-side job fails with
-- "permission denied for table ...".
grant select, insert, update, delete
  on public.profiles, public.products, public.reminders
  to service_role;


-- ── Enable RLS ──────────────────────────────────────────────────────────────
alter table public.profiles  enable row level security;
alter table public.products  enable row level security;
alter table public.reminders enable row level security;


-- ── profiles ────────────────────────────────────────────────────────────────
-- A user may read and update only their own profile. Inserts are handled by the
-- handle_new_user() trigger, and is_premium is protected below, so there is
-- deliberately no INSERT or DELETE policy here.

create policy "Users can view own profile"
  on public.profiles for select
  to authenticated
  using ((select auth.uid()) = id);

create policy "Users can update own profile"
  on public.profiles for update
  to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);


-- ── products ────────────────────────────────────────────────────────────────
create policy "Users can view own products"
  on public.products for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users can insert own products"
  on public.products for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

create policy "Users can update own products"
  on public.products for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "Users can delete own products"
  on public.products for delete
  to authenticated
  using ((select auth.uid()) = user_id);


-- ── reminders ───────────────────────────────────────────────────────────────
-- Reminder rows are created and refreshed by a trigger, and delivered by the
-- send-reminders Edge Function (service role, which bypasses RLS). The client
-- only ever needs to read them, plus delete when tidying up a product.

create policy "Users can view own reminders"
  on public.reminders for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users can delete own reminders"
  on public.reminders for delete
  to authenticated
  using ((select auth.uid()) = user_id);


-- ── Protect the freemium flag ───────────────────────────────────────────────
-- The update policy above would otherwise let a client simply set
-- is_premium = true on its own row and unlock the paid tier. Only billing may
-- change that column, so client-originated changes are rejected.
--
-- The check is on current_user, which PostgREST sets to 'authenticated' (or
-- 'anon') for app traffic while the service role and admins connect as
-- themselves. This is deliberately SECURITY INVOKER: a SECURITY DEFINER
-- function would report its owner as current_user and block everyone,
-- including support staff comping an account from the SQL editor.

create or replace function public.prevent_premium_self_grant()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if new.is_premium is distinct from old.is_premium
     and current_user in ('authenticated', 'anon') then
    raise exception
      'FORBIDDEN: is_premium can only be changed by billing, not by the client.'
      using errcode = 'PT403';
  end if;
  return new;
end;
$$;

create trigger profiles_protect_is_premium
  before update on public.profiles
  for each row execute function public.prevent_premium_self_grant();
