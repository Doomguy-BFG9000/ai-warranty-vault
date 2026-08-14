-- ============================================================================
-- Let a reminder be read together with the profile it belongs to.
--
-- reminders.user_id already references auth.users, and profiles.id references
-- auth.users as well — but PostgREST will not traverse that indirect hop, so
-- `reminders?select=...,profiles(expo_push_token)` fails with PGRST200.
--
-- The send-reminders job needs exactly that pairing: the reminder says when,
-- the profile says where to deliver it. Declaring the relationship directly
-- makes it a single query instead of a manual join in application code.
--
-- Safe to add: profiles.id *is* auth.users.id (the row is created by the
-- handle_new_user() signup trigger), so every existing reminder already has a
-- matching profile.
-- ============================================================================

alter table public.reminders
  add constraint reminders_user_id_profiles_fkey
  foreign key (user_id) references public.profiles (id) on delete cascade;

comment on constraint reminders_user_id_profiles_fkey on public.reminders is
  'Lets send-reminders read a reminder and its delivery target in one query.';
