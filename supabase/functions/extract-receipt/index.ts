/**
 * extract-receipt — turns a stored receipt image into structured product data.
 *
 * Flow: client uploads to the private `receipts` bucket, then calls this with the
 * storage path. The function downloads the image **using the caller's own JWT**,
 * so Storage RLS decides what they may read — a user cannot extract someone
 * else's receipt even by guessing a path. It then asks Claude to read the image
 * and return data matching a strict JSON schema.
 *
 * The ANTHROPIC_API_KEY lives only in this function's environment. It is never
 * shipped to the app, which is the whole reason extraction runs server-side.
 */
import Anthropic from '@anthropic-ai/sdk';
import { createClient } from '@supabase/supabase-js';

import { RECEIPT_EXTRACTION_SCHEMA } from '../_shared/schema.ts';
import { corsHeaders, jsonResponse } from '../_shared/http.ts';

const RECEIPTS_BUCKET = 'receipts';

/**
 * Claude Opus 5 is the default. Extraction is a short, high-volume call, so if
 * cost per receipt matters more than accuracy, set CLAUDE_MODEL to a cheaper
 * model (e.g. claude-sonnet-5-5) without touching this code.
 */
const MODEL = Deno.env.get('CLAUDE_MODEL') ?? 'claude-opus-5';

const SYSTEM_PROMPT = `You read retail receipts and extract warranty-relevant purchase details.

Guidance:
- Extract only what is actually printed. Never infer a warranty length from the
  product type — if the receipt does not state a warranty, return null.
- When a receipt lists several items, choose the single most substantial product
  (highest price, or the one whose warranty is mentioned). This app tracks one
  product per receipt.
- Prefer the item's own line price over the order total, so tax and unrelated
  items are excluded.
- Convert any printed date format to YYYY-MM-DD. A date like 03/14/2026 on a
  US receipt is March 14; on a European receipt the same digits may be day-first
  — use the receipt's locale, currency, and address to decide.
- Set confidence to reflect how legible the image was, and not_a_receipt when
  the image plainly is not a receipt or invoice.`;

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  if (req.method !== 'POST') {
    return jsonResponse({ error: 'Method not allowed' }, 405);
  }

  try {
    const apiKey = Deno.env.get('ANTHROPIC_API_KEY');
    if (!apiKey) {
      console.error('ANTHROPIC_API_KEY is not set for this function');
      return jsonResponse({ error: 'Extraction is not configured.' }, 500);
    }

    const authHeader = req.headers.get('Authorization');
    if (!authHeader) {
      return jsonResponse({ error: 'Missing authorization header.' }, 401);
    }

    let body: { receiptPath?: string };
    try {
      body = await req.json();
    } catch {
      return jsonResponse({ error: 'Request body must be JSON.' }, 400);
    }

    const receiptPath = body.receiptPath?.trim();
    if (!receiptPath) {
      return jsonResponse({ error: 'receiptPath is required.' }, 400);
    }

    // Acting as the caller, not the service role: Storage RLS is what stops one
    // user from extracting another user's receipt.
    const supabase = createClient(
      Deno.env.get('SUPABASE_URL')!,
      Deno.env.get('SUPABASE_ANON_KEY')!,
      { global: { headers: { Authorization: authHeader } } },
    );

    const { data: file, error: downloadError } = await supabase.storage
      .from(RECEIPTS_BUCKET)
      .download(receiptPath);

    if (downloadError || !file) {
      // A missing object and someone else's object are indistinguishable here,
      // which is the desired behaviour — do not confirm that a path exists.
      console.error('receipt download failed', downloadError?.message);
      return jsonResponse({ error: 'Receipt image could not be read.' }, 404);
    }

    const mediaType = normaliseMediaType(file.type);
    if (!mediaType) {
      return jsonResponse({ error: 'Unsupported image format.' }, 415);
    }

    const base64 = encodeBase64(await file.arrayBuffer());

    const anthropic = new Anthropic({ apiKey });

    const message = await anthropic.messages.create({
      model: MODEL,
      max_tokens: 4096,
      system: SYSTEM_PROMPT,
      // Extraction is a short, well-bounded read. Low effort keeps latency and
      // cost down; thinking stays on, which avoids the failure modes that come
      // with disabling it outright.
      output_config: {
        effort: 'low',
        format: { type: 'json_schema', schema: RECEIPT_EXTRACTION_SCHEMA },
      },
      messages: [
        {
          role: 'user',
          content: [
            { type: 'image', source: { type: 'base64', media_type: mediaType, data: base64 } },
            { type: 'text', text: 'Extract the purchase details from this receipt.' },
          ],
        },
      ],
    });

    if (message.stop_reason === 'refusal') {
      console.error('extraction refused', message.stop_details);
      return jsonResponse({ error: 'This image could not be processed.' }, 422);
    }

    const textBlock = message.content.find((block: { type: string }) => block.type === 'text');
    if (!textBlock || textBlock.type !== 'text') {
      console.error('no text block in response', message.stop_reason);
      return jsonResponse({ error: 'Extraction returned no result.' }, 502);
    }

    // Structured outputs constrain the response to the schema, so this parses
    // without the usual repair/retry dance.
    const extraction = JSON.parse(textBlock.text);

    return jsonResponse({
      extraction,
      usage: {
        input_tokens: message.usage.input_tokens,
        output_tokens: message.usage.output_tokens,
      },
    });
  } catch (error) {
    console.error('extract-receipt failed', error);
    return jsonResponse({ error: 'Extraction failed. Please try again.' }, 500);
  }
});

/** Maps a stored file's content type onto the media types the vision API accepts. */
function normaliseMediaType(
  type: string | undefined,
): 'image/jpeg' | 'image/png' | 'image/webp' | 'image/gif' | null {
  switch (type) {
    case 'image/jpeg':
    case 'image/jpg':
      return 'image/jpeg';
    case 'image/png':
      return 'image/png';
    case 'image/webp':
      return 'image/webp';
    case 'image/gif':
      return 'image/gif';
    default:
      // Images are re-encoded to JPEG on the device before upload, so an unknown
      // type here means the object did not come through the normal path.
      return null;
  }
}

/** Base64-encodes in chunks; spreading a large array into fromCharCode overflows the stack. */
function encodeBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const CHUNK = 0x8000;
  let binary = '';
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}
