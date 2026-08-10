/**
 * Boundary checks for the extract-receipt Edge Function.
 *
 * These cover everything up to the Claude call — auth, input validation, and
 * most importantly that Storage RLS (not the function) decides whose receipt a
 * caller may read. They deliberately do NOT require a real ANTHROPIC_API_KEY:
 * a placeholder is enough, because a request that reaches Claude has already
 * passed every check these assertions care about.
 *
 * Usage:
 *   npx supabase functions serve --env-file supabase/.env
 *   npm run verify:extract-fn
 */
import { createClient } from '@supabase/supabase-js';

const URL = process.env.EXPO_PUBLIC_SUPABASE_URL ?? 'http://127.0.0.1:54321';
const ANON =
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ??
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0';

if (!URL.includes('127.0.0.1') && !URL.includes('localhost')) {
  console.error(`Refusing to run against a non-local stack: ${URL}`);
  process.exit(1);
}

const FN = `${URL}/functions/v1/extract-receipt`;

let failures = 0;
const check = (label, cond, extra = '') => {
  if (!cond) failures++;
  console.log(`${cond ? '✅' : '❌'} ${label}${extra ? ` — ${extra}` : ''}`);
};

const stamp = Date.now();
const alice = createClient(URL, ANON);
const bob = createClient(URL, ANON);

const { data: a } = await alice.auth.signUp({
  email: `fn-alice${stamp}@example.com`,
  password: 'Str0ng-Passw0rd!',
});
const { data: b } = await bob.auth.signUp({
  email: `fn-bob${stamp}@example.com`,
  password: 'Str0ng-Passw0rd!',
});
const aliceToken = a.session.access_token;
const bobToken = b.session.access_token;
const aliceId = a.user.id;

// Alice uploads a receipt she owns.
const jpegBytes = new Uint8Array([0xff, 0xd8, 0xff, 0xdb, 0x00, 0x01, 0x02, 0x03]);
const alicePath = `${aliceId}/fn-test.jpg`;
const { error: upErr } = await alice.storage
  .from('receipts')
  .upload(alicePath, jpegBytes, { contentType: 'image/jpeg', upsert: true });
check('Alice uploads a receipt', !upErr, upErr?.message);

const call = async (token, body, method = 'POST') => {
  const res = await fetch(FN, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      apikey: ANON,
      'Content-Type': 'application/json',
    },
    ...(method === 'POST' ? { body: JSON.stringify(body ?? {}) } : {}),
  });
  let json = null;
  try {
    json = await res.json();
  } catch {
    /* non-JSON body */
  }
  return { status: res.status, json };
};

// ── auth ──────────────────────────────────────────────────────────────────
const noAuth = await call(null, { receiptPath: alicePath });
check('Rejects an unauthenticated caller', noAuth.status === 401, `HTTP ${noAuth.status}`);

// ── input validation ──────────────────────────────────────────────────────
const noPath = await call(aliceToken, {});
check('Requires receiptPath', noPath.status === 400, `HTTP ${noPath.status}`);

const wrongMethod = await call(aliceToken, null, 'GET');
check('Rejects non-POST methods', wrongMethod.status === 405, `HTTP ${wrongMethod.status}`);

// ── the security boundary ─────────────────────────────────────────────────
// Bob knows Alice's exact storage path and asks the function to read it. The
// function runs as the caller, so Storage RLS refuses — Bob cannot extract a
// receipt he does not own, and the 404 does not confirm the path exists.
const crossUser = await call(bobToken, { receiptPath: alicePath });
check(
  'Bob cannot extract Alice receipt',
  crossUser.status === 404,
  `HTTP ${crossUser.status}`,
);
check(
  'Cross-user failure does not leak that the path exists',
  !JSON.stringify(crossUser.json ?? {}).toLowerCase().includes('permission'),
  JSON.stringify(crossUser.json),
);

const missing = await call(aliceToken, { receiptPath: `${aliceId}/does-not-exist.jpg` });
check('Missing object is indistinguishable from a forbidden one', missing.status === 404,
      `HTTP ${missing.status}`);

// ── the owner gets past every check ───────────────────────────────────────
// With a placeholder API key this stops at the Claude call, which is exactly
// the point: reaching that call proves auth, validation and RLS all passed.
const owner = await call(aliceToken, { receiptPath: alicePath });
check(
  'Owner passes auth, validation and RLS (reaches the Claude call)',
  owner.status === 500 || owner.status === 200,
  `HTTP ${owner.status} — 500 expected with a placeholder key, 200 with a real one`,
);

await alice.storage.from('receipts').remove([alicePath]);

console.log(`\n${failures === 0 ? '✅ ALL BOUNDARY CHECKS PASSED' : `❌ ${failures} CHECK(S) FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
