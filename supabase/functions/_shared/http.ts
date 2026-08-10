/**
 * Shared HTTP helpers for Edge Functions.
 *
 * The app calls these functions from a native runtime rather than a browser, so
 * CORS is only strictly needed for Expo's web target and local testing — it is
 * permissive here because every function is already behind JWT verification.
 */

export const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, 'Content-Type': 'application/json' },
  });
}
