/**
 * send-reminders — delivers the warranty reminders that have come due.
 *
 * Runs on a schedule (pg_cron), not from the app, so it uses the service role
 * and reads across every user. It selects only reminders that are due and not
 * yet sent, pushes them through the Expo Push API, and stamps sent_at so a
 * reminder is never delivered twice.
 *
 * Reminder *rows* are created by the sync_product_reminders() database trigger
 * whenever a product's expiry changes; this function only delivers them.
 */
import { createClient } from '@supabase/supabase-js';

import { corsHeaders, jsonResponse } from '../_shared/http.ts';

const EXPO_PUSH_ENDPOINT = 'https://exp.host/--/api/v2/push/send';

/** Expo accepts up to 100 messages per request. */
const PUSH_CHUNK_SIZE = 100;

/** Bounds one run so a backlog cannot stall the schedule. */
const MAX_REMINDERS_PER_RUN = 500;

interface DueReminder {
  id: string;
  remind_at: string;
  products: {
    name: string;
    merchant: string | null;
    warranty_expires_at: string | null;
  } | null;
  profiles: { expo_push_token: string | null } | null;
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  if (req.method !== 'POST') {
    return jsonResponse({ error: 'Method not allowed' }, 405);
  }

  // The schedule calls this with the service-role key; nothing user-facing
  // should be able to trigger a send.
  const auth = req.headers.get('Authorization');
  const serviceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
  if (!serviceKey || auth !== `Bearer ${serviceKey}`) {
    return jsonResponse({ error: 'Unauthorized' }, 401);
  }

  try {
    const supabase = createClient(Deno.env.get('SUPABASE_URL')!, serviceKey);

    // Join through to the product (what to say) and the profile (where to send).
    const { data, error } = await supabase
      .from('reminders')
      .select(
        'id, remind_at, products!inner(name, merchant, warranty_expires_at), profiles!inner(expo_push_token)',
      )
      .is('sent_at', null)
      .lte('remind_at', new Date().toISOString())
      .order('remind_at', { ascending: true })
      .limit(MAX_REMINDERS_PER_RUN);

    if (error) throw error;

    const due = (data ?? []) as unknown as DueReminder[];
    if (due.length === 0) {
      return jsonResponse({ due: 0, sent: 0, skipped: 0 });
    }

    // A user without a push token still gets the local notification the app
    // scheduled, so mark these delivered rather than retrying them forever.
    const deliverable = due.filter((r) => r.profiles?.expo_push_token && r.products);
    const skipped = due.filter((r) => !r.profiles?.expo_push_token || !r.products);

    const messages = deliverable.map((reminder) => ({
      to: reminder.profiles!.expo_push_token,
      title: reminderTitle(reminder),
      body: reminderBody(reminder),
      data: { reminderId: reminder.id },
      sound: 'default',
    }));

    const sentIds: string[] = [];
    const failedIds: string[] = [];

    for (let i = 0; i < messages.length; i += PUSH_CHUNK_SIZE) {
      const chunk = messages.slice(i, i + PUSH_CHUNK_SIZE);
      const chunkIds = deliverable.slice(i, i + PUSH_CHUNK_SIZE).map((r) => r.id);

      try {
        const response = await fetch(EXPO_PUSH_ENDPOINT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(chunk),
        });

        if (!response.ok) {
          console.error('expo push rejected chunk', response.status);
          failedIds.push(...chunkIds);
          continue;
        }

        // Per-ticket status: a device that uninstalled shows up here as an
        // error, and must not be retried on the next run.
        const body = (await response.json()) as {
          data?: { status: string; message?: string }[];
        };

        (body.data ?? []).forEach((ticket, index) => {
          const id = chunkIds[index];
          if (ticket.status === 'ok') {
            sentIds.push(id);
          } else {
            console.error('push ticket error', ticket.message);
            failedIds.push(id);
          }
        });
      } catch (err) {
        // Network failure — leave sent_at null so the next run retries.
        console.error('push chunk failed', err);
        failedIds.push(...chunkIds);
      }
    }

    // Stamp delivered reminders, plus the ones with nowhere to send: both are
    // finished as far as this queue is concerned.
    const toStamp = [...sentIds, ...skipped.map((r) => r.id)];
    if (toStamp.length > 0) {
      const { error: stampError } = await supabase
        .from('reminders')
        .update({ sent_at: new Date().toISOString() })
        .in('id', toStamp);

      if (stampError) throw stampError;
    }

    return jsonResponse({
      due: due.length,
      sent: sentIds.length,
      skipped: skipped.length,
      failed: failedIds.length,
    });
  } catch (error) {
    console.error('send-reminders failed', error);
    return jsonResponse({ error: 'Reminder run failed.' }, 500);
  }
});

function daysUntil(remindAt: string, expiresAt: string | null): number {
  if (!expiresAt) return 0;
  const expiry = new Date(`${expiresAt}T00:00:00Z`).getTime();
  const remind = new Date(remindAt).getTime();
  return Math.max(0, Math.round((expiry - remind) / 86_400_000));
}

function reminderTitle(reminder: DueReminder): string {
  return daysUntil(reminder.remind_at, reminder.products!.warranty_expires_at) >= 30
    ? 'Warranty expiring next month'
    : 'Warranty expiring soon';
}

function reminderBody(reminder: DueReminder): string {
  const product = reminder.products!;
  const days = daysUntil(reminder.remind_at, product.warranty_expires_at);
  const where = product.merchant ? ` from ${product.merchant}` : '';
  const when = days === 1 ? 'tomorrow' : `in ${days} days`;
  return `${product.name}${where} is covered until ${product.warranty_expires_at}. Cover ends ${when}.`;
}
