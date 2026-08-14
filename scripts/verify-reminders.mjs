/**
 * Checks the reminder pipeline against a real local stack.
 *
 * Covers what can be asserted without a device: that the database trigger
 * schedules and reschedules reminder rows correctly, that send-reminders is
 * closed to anyone but the scheduler, and that a due reminder is claimed
 * exactly once so a user cannot be notified twice for the same window.
 *
 * Usage:
 *   npm run db:start && npm run fn:serve   # in another shell
 *   npm run verify:reminders
 */
import { createClient } from '@supabase/supabase-js';

const URL = process.env.EXPO_PUBLIC_SUPABASE_URL ?? 'http://127.0.0.1:54321';
const ANON =
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ??
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0';
const SERVICE =
  process.env.SUPABASE_SERVICE_ROLE_KEY ??
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU';

if (!URL.includes('127.0.0.1') && !URL.includes('localhost')) {
  console.error(`Refusing to run against a non-local stack: ${URL}`);
  process.exit(1);
}

const FN = `${URL}/functions/v1/send-reminders`;

let failures = 0;
const check = (label, cond, extra = '') => {
  if (!cond) failures++;
  console.log(`${cond ? '✅' : '❌'} ${label}${extra ? ` — ${extra}` : ''}`);
};

const admin = createClient(URL, SERVICE);
const stamp = Date.now();

const user = createClient(URL, ANON);
const { data: signUp, error: signUpError } = await user.auth.signUp({
  email: `rem${stamp}@example.com`,
  password: 'Str0ng-Passw0rd!',
});
if (signUpError) throw signUpError;
const userId = signUp.user.id;

const iso = (d) => d.toISOString().slice(0, 10);
const inDays = (days) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d;
};

// ── the trigger schedules the right windows ─────────────────────────────────
const { data: product, error: insertError } = await user
  .from('products')
  .insert({
    user_id: userId,
    name: 'Reminder Test Product',
    // Expiring in 60 days: both the 30-day and 7-day windows are still ahead.
    purchase_date: iso(inDays(60 - 365)),
    warranty_months: 12,
  })
  .select()
  .single();
check('Product insert succeeds', !insertError, insertError?.message);

const remindersFor = async (productId) => {
  const { data } = await admin
    .from('reminders')
    .select('id, remind_at, sent_at')
    .eq('product_id', productId)
    .order('remind_at');
  return data ?? [];
};

const scheduled = await remindersFor(product.id);
check('Trigger schedules both future windows', scheduled.length === 2,
      `${scheduled.length} reminder(s)`);

const daysOut = scheduled.map((r) =>
  Math.round((new Date(r.remind_at) - new Date(product.warranty_expires_at)) / 86_400_000),
);
check('Windows sit 30 and 7 days before expiry', daysOut.every((d) => d === -30 || d === -7),
      daysOut.join(', '));

// ── rescheduling on edit ────────────────────────────────────────────────────
await user
  .from('products')
  .update({ warranty_months: 24 })
  .eq('id', product.id);

const rescheduled = await remindersFor(product.id);
const movedForward = rescheduled.every(
  (r) => new Date(r.remind_at) > new Date(scheduled[1].remind_at),
);
check('Extending the warranty moves the reminders out', movedForward);
check('Rescheduling does not accumulate rows', rescheduled.length === 2,
      `${rescheduled.length} reminder(s)`);

// ── a past window is not scheduled ──────────────────────────────────────────
const { data: old } = await user
  .from('products')
  .insert({
    user_id: userId,
    name: 'Long Expired Product',
    purchase_date: iso(inDays(-1000)),
    warranty_months: 12,
  })
  .select()
  .single();
check('No reminders for an already-expired warranty', (await remindersFor(old.id)).length === 0);

// ── the function is closed to everyone but the scheduler ────────────────────
const call = async (token) => {
  const res = await fetch(FN, {
    method: 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      apikey: ANON,
      'Content-Type': 'application/json',
    },
    body: '{}',
  });
  let json = null;
  try {
    json = await res.json();
  } catch {
    /* non-JSON body */
  }
  return { status: res.status, json };
};

check('Rejects an unauthenticated caller', (await call(null)).status === 401);
check('Rejects a signed-in user token', (await call(signUp.session.access_token)).status === 401,
      'a user must not be able to trigger a send');

// ── delivery claims a due reminder exactly once ─────────────────────────────
// Pull one window into the past so this run has something to deliver. The user
// has no push token, so it is "skipped" — which still stamps sent_at, because
// there is nowhere to deliver it and retrying forever would be pointless.
const target = rescheduled[0];
await admin
  .from('reminders')
  .update({ remind_at: new Date(Date.now() - 60_000).toISOString() })
  .eq('id', target.id);

const first = await call(SERVICE);
check('Scheduler call is accepted', first.status === 200, `HTTP ${first.status}`);
check('The due reminder is picked up', (first.json?.due ?? 0) >= 1, JSON.stringify(first.json));

const afterFirst = (await remindersFor(product.id)).find((r) => r.id === target.id);
check('A reminder with nowhere to send is not left to retry forever',
      afterFirst?.sent_at !== null, `sent_at=${afterFirst?.sent_at}`);

const second = await call(SERVICE);
check('A second run does not redeliver it', (second.json?.due ?? 0) === 0,
      JSON.stringify(second.json));

// ── cleanup ─────────────────────────────────────────────────────────────────
await admin.auth.admin.deleteUser(userId);

console.log(`\n${failures === 0 ? '✅ ALL REMINDER CHECKS PASSED' : `❌ ${failures} CHECK(S) FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
