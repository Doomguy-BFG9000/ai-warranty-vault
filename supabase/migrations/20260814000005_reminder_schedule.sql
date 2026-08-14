-- ============================================================================
-- Daily delivery of due warranty reminders.
--
-- The reminder *rows* are already maintained by sync_product_reminders(); this
-- migration only schedules their delivery. pg_cron calls the send-reminders
-- Edge Function over HTTP (pg_net), because sending a push is not something
-- Postgres should be doing itself.
--
-- Two settings must exist for the job to work. They are per-project secrets, so
-- they are set out of band rather than committed here:
--
--   alter database postgres set app.settings.functions_url = 'https://<ref>.supabase.co/functions/v1';
--   alter database postgres set app.settings.service_role_key = '<service role key>';
--
-- Until they are set the job runs and logs a notice instead of firing blindly.
-- ============================================================================

create extension if not exists pg_cron with schema extensions;
create extension if not exists pg_net with schema extensions;

-- ── the job body ────────────────────────────────────────────────────────────
create or replace function public.dispatch_due_reminders()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_functions_url text := current_setting('app.settings.functions_url', true);
  v_service_key   text := current_setting('app.settings.service_role_key', true);
begin
  if v_functions_url is null or v_service_key is null then
    -- Local stacks and fresh projects have no secrets configured. Say so once
    -- per run rather than issuing a request that is guaranteed to 401.
    raise notice 'dispatch_due_reminders: functions_url/service_role_key not configured, skipping';
    return;
  end if;

  perform extensions.net.http_post(
    url     := v_functions_url || '/send-reminders',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || v_service_key
    ),
    body    := '{}'::jsonb
  );
end;
$$;

comment on function public.dispatch_due_reminders is
  'Asks the send-reminders Edge Function to deliver reminders that have come due.';

-- Only the scheduler should be able to call this.
revoke all on function public.dispatch_due_reminders() from public, anon, authenticated;

-- ── the schedule ────────────────────────────────────────────────────────────
-- Hourly rather than daily: reminder rows are timestamped at 09:00, and an
-- hourly scan delivers them close to that time in whatever timezone the
-- project runs in, without needing per-user scheduling.
do $$
begin
  if exists (select 1 from cron.job where jobname = 'dispatch-due-reminders') then
    perform cron.unschedule('dispatch-due-reminders');
  end if;

  perform cron.schedule(
    'dispatch-due-reminders',
    '0 * * * *',
    $cron$select public.dispatch_due_reminders()$cron$
  );
end;
$$;
