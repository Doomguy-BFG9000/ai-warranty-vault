/**
 * End-to-end verification of the backend through the real PostgREST API using
 * the anon key — exactly how the mobile app talks to Supabase. This exercises
 * the security boundary (RLS, storage isolation, the freemium cap) rather than
 * trusting that the SQL is right.
 *
 * Usage:
 *   npx supabase start && npm run db:reset
 *   npm run verify:backend
 *
 * Expects a freshly reset local stack. Reads connection details from the
 * environment, defaulting to the Supabase CLI's well-known local values (these
 * are fixed public constants of the local dev stack, not secrets).
 */
import { createClient } from '@supabase/supabase-js';

const URL = process.env.EXPO_PUBLIC_SUPABASE_URL ?? 'http://127.0.0.1:54321';
const ANON =
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ??
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0';

if (!URL.includes('127.0.0.1') && !URL.includes('localhost')) {
  console.error('Refusing to run: this script creates test users and uploads files.');
  console.error(`It is intended for a local stack only, but got: ${URL}`);
  process.exit(1);
}

const ok = (b) => (b ? '✅' : '❌');
let failures = 0;
const check = (label, cond, extra = '') => {
  if (!cond) failures++;
  console.log(`${ok(cond)} ${label}${extra ? ` — ${extra}` : ''}`);
};

const stamp = Date.now();
const alice = createClient(URL, ANON);
const bob = createClient(URL, ANON);

// ── signup ────────────────────────────────────────────────────────────────
const aliceEmail = `alice${stamp}@example.com`;
const bobEmail = `bob${stamp}@example.com`;

const { data: aSign, error: aErr } = await alice.auth.signUp({
  email: aliceEmail, password: 'Str0ng-Passw0rd!',
});
check('Alice signs up', !aErr && !!aSign.session, aErr?.message);

const { data: bSign, error: bErr } = await bob.auth.signUp({
  email: bobEmail, password: 'Str0ng-Passw0rd!',
});
check('Bob signs up', !bErr && !!bSign.session, bErr?.message);

const aliceId = aSign.user.id;

// ── profile trigger ───────────────────────────────────────────────────────
const { data: prof, error: profErr } = await alice.from('profiles').select('*');
check('Profile auto-created and readable', !profErr && prof?.length === 1, profErr?.message);
check('New user defaults to free tier', prof?.[0]?.is_premium === false);
check('Profile email populated by trigger', prof?.[0]?.email === aliceEmail);

// ── insert + generated column ─────────────────────────────────────────────
const { data: p1, error: p1Err } = await alice.from('products')
  .insert({ user_id: aliceId, name: 'MacBook Pro', merchant: 'Apple',
            purchase_date: '2026-03-01', warranty_months: 24, price_cents: 249900 })
  .select().single();
check('Insert product', !p1Err, p1Err?.message);
check('warranty_expires_at generated server-side', p1?.warranty_expires_at === '2028-03-01',
      `got ${p1?.warranty_expires_at}`);

// ── reminders auto-created ────────────────────────────────────────────────
const { data: rems } = await alice.from('reminders').select('*').order('remind_at');
check('Two reminders auto-created', rems?.length === 2, `got ${rems?.length}`);

// ── writing the generated column is rejected ──────────────────────────────
const { error: genErr } = await alice.from('products')
  .update({ warranty_expires_at: '2099-01-01' }).eq('id', p1.id);
check('Cannot write generated column', !!genErr, genErr?.message?.slice(0, 60));

// ── freemium cap via HTTP ─────────────────────────────────────────────────
for (let i = 2; i <= 5; i++) {
  const { error } = await alice.from('products').insert({ user_id: aliceId, name: `Item ${i}` });
  if (error) check(`Insert #${i} within free tier`, false, error.message);
}
const { count } = await alice.from('products').select('*', { count: 'exact', head: true });
check('Free tier holds exactly 5 products', count === 5, `got ${count}`);

const { error: capErr, status: capStatus } = await alice.from('products')
  .insert({ user_id: aliceId, name: 'Sixth item' });
check('6th insert rejected', !!capErr, capErr?.message?.slice(0, 50));
check('PT402 surfaces as HTTP 402 Payment Required', capStatus === 402, `got HTTP ${capStatus}`);
check('Error message is machine-matchable', capErr?.message?.includes('FREE_TIER_LIMIT_REACHED'));

// ── premium self-grant blocked over HTTP ──────────────────────────────────
const { error: premErr, status: premStatus } = await alice.from('profiles')
  .update({ is_premium: true }).eq('id', aliceId);
check('Client cannot self-grant premium', !!premErr, premErr?.message?.slice(0, 50));
check('PT403 surfaces as HTTP 403 Forbidden', premStatus === 403, `got HTTP ${premStatus}`);

// ── cross-user isolation over HTTP ────────────────────────────────────────
const { data: bobProducts } = await bob.from('products').select('*');
check('Bob sees none of Alice products', bobProducts?.length === 0, `got ${bobProducts?.length}`);

const { data: bobRems } = await bob.from('reminders').select('*');
check('Bob sees none of Alice reminders', bobRems?.length === 0, `got ${bobRems?.length}`);

const { error: stealErr } = await bob.from('products')
  .insert({ user_id: aliceId, name: 'Stolen' });
check('Bob cannot insert as Alice', !!stealErr, stealErr?.message?.slice(0, 45));
check('Cross-user insert gives RLS error, not a quota leak',
      !!stealErr && !stealErr.message.includes('FREE_TIER_LIMIT_REACHED'),
      stealErr?.message?.slice(0, 45));

const { data: delData } = await bob.from('products').delete().eq('id', p1.id).select();
check('Bob cannot delete Alice product', (delData?.length ?? 0) === 0);

// ── anonymous access ──────────────────────────────────────────────────────
const anon = createClient(URL, ANON);
const { data: anonData, error: anonErr } = await anon.from('products').select('*');
check('Signed-out client reads nothing', !!anonErr || anonData?.length === 0,
      anonErr?.message?.slice(0, 40));

// ── storage isolation ─────────────────────────────────────────────────────
const bytes = new Uint8Array([0xff, 0xd8, 0xff, 0xdb, 0x00, 0x01]);
const { error: upOwn } = await alice.storage.from('receipts')
  .upload(`${aliceId}/own.jpg`, bytes, { contentType: 'image/jpeg' });
check('User uploads to own folder', !upOwn, upOwn?.message);

const { error: upOther } = await bob.storage.from('receipts')
  .upload(`${aliceId}/intruder.jpg`, bytes, { contentType: 'image/jpeg' });
check('User cannot upload into another user folder', !!upOther, upOther?.message?.slice(0, 45));

const { data: signed, error: signErr } = await alice.storage.from('receipts')
  .createSignedUrl(`${aliceId}/own.jpg`, 60);
check('Owner can mint a signed URL', !signErr && !!signed?.signedUrl, signErr?.message);

const pub = await fetch(`${URL}/storage/v1/object/public/receipts/${aliceId}/own.jpg`);
check('Bucket is private (public URL blocked)', pub.status >= 400, `HTTP ${pub.status}`);

// ── limit function readable by client ─────────────────────────────────────
const { data: limit, error: limErr } = await alice.rpc('free_tier_product_limit');
check('Client can read free-tier limit via RPC', !limErr && limit === 5, limErr?.message);

console.log(`\n${failures === 0 ? '✅ ALL CHECKS PASSED' : `❌ ${failures} CHECK(S) FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
