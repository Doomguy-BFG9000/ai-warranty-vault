import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { getWarrantyState } from '@/lib/warranty';
import type { Product } from '@/types/models';

/**
 * Notification plumbing: permissions, the Expo push token, and the on-device
 * fallback schedule.
 *
 * Reminders are delivered two ways on purpose. The server cron is the primary
 * path — it works with the app closed and survives a reinstall. Locally
 * scheduled notifications are the fallback: they still fire if push delivery
 * fails, if the user declined a push token, or if the device is offline when
 * the cron runs.
 */

/** Matches the offsets the sync_product_reminders() database trigger uses. */
export const REMINDER_OFFSET_DAYS = [30, 7] as const;

/** Late morning, so a reminder never arrives in the middle of the night. */
const REMINDER_HOUR = 9;

/** Foreground behaviour. Without this a notification is silent while the app is open. */
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export interface PushRegistration {
  token: string | null;
  /** False when the user declined, or the OS cannot deliver push at all. */
  granted: boolean;
}

/**
 * Asks for notification permission and, if granted, resolves the Expo push token.
 *
 * A null token is a normal outcome, not an error: simulators have no push
 * capability, and a user may decline. Local reminders still work in that case.
 */
export async function registerForPushNotifications(): Promise<PushRegistration> {
  if (Platform.OS === 'android') {
    // Android needs a channel before anything is delivered.
    await Notifications.setNotificationChannelAsync('warranty-reminders', {
      name: 'Warranty reminders',
      importance: Notifications.AndroidImportance.DEFAULT,
    });
  }

  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;

  if (status !== 'granted') {
    const requested = await Notifications.requestPermissionsAsync();
    status = requested.status;
  }

  if (status !== 'granted') return { token: null, granted: false };

  // Push tokens require real hardware; a simulator can still show local ones.
  if (!Device.isDevice) return { token: null, granted: true };

  try {
    const { data } = await Notifications.getExpoPushTokenAsync();
    return { token: data, granted: true };
  } catch {
    // Offline or missing push credentials — permission still holds, so local
    // reminders remain available.
    return { token: null, granted: true };
  }
}

/**
 * Rebuilds this product's on-device reminders.
 *
 * Notification identifiers are derived from the product id so a re-sync can
 * cancel exactly the ones it previously scheduled, rather than clearing every
 * notification the app owns.
 */
export async function syncLocalReminders(product: Product): Promise<number> {
  await cancelLocalReminders(product.id);

  if (!product.warranty_expires_at) return 0;

  const expiry = new Date(`${product.warranty_expires_at}T00:00:00`);
  let scheduled = 0;

  for (const offsetDays of REMINDER_OFFSET_DAYS) {
    const fireAt = new Date(expiry);
    fireAt.setDate(fireAt.getDate() - offsetDays);
    fireAt.setHours(REMINDER_HOUR, 0, 0, 0);

    // A window already past cannot be scheduled — common when adding an old receipt.
    if (fireAt.getTime() <= Date.now()) continue;

    await Notifications.scheduleNotificationAsync({
      identifier: localReminderId(product.id, offsetDays),
      content: {
        title: reminderTitle(offsetDays),
        body: reminderBody(product, offsetDays),
        data: { productId: product.id },
      },
      trigger: {
        type: Notifications.SchedulableTriggerInputTypes.DATE,
        date: fireAt,
      },
    });
    scheduled++;
  }

  return scheduled;
}

export async function cancelLocalReminders(productId: string): Promise<void> {
  await Promise.all(
    REMINDER_OFFSET_DAYS.map((offsetDays) =>
      Notifications.cancelScheduledNotificationAsync(
        localReminderId(productId, offsetDays),
      ).catch(() => {
        // Cancelling something that was never scheduled is not a failure.
      }),
    ),
  );
}

function localReminderId(productId: string, offsetDays: number): string {
  return `warranty-${productId}-${offsetDays}`;
}

export function reminderTitle(offsetDays: number): string {
  return offsetDays >= 30 ? 'Warranty expiring next month' : 'Warranty expiring soon';
}

export function reminderBody(
  product: Pick<Product, 'name' | 'merchant' | 'warranty_expires_at'>,
  offsetDays: number,
): string {
  const where = product.merchant ? ` from ${product.merchant}` : '';
  const when = offsetDays === 1 ? 'tomorrow' : `in ${offsetDays} days`;
  return `${product.name}${where} is covered until ${product.warranty_expires_at}. Cover ends ${when}.`;
}

/** Human summary of what is scheduled, for the settings screen. */
export function describeNextReminder(product: Product): string | null {
  const state = getWarrantyState(product.warranty_expires_at);
  if (state.status === 'unknown' || state.status === 'expired') return null;
  return state.label;
}
