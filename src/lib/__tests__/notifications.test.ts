import { REMINDER_OFFSET_DAYS, reminderBody, reminderTitle } from '@/lib/notifications';

// expo-notifications touches native modules at import time; the message
// builders under test are pure, so the native surface is stubbed away.
jest.mock('expo-notifications', () => ({
  setNotificationHandler: jest.fn(),
  setNotificationChannelAsync: jest.fn(),
  getPermissionsAsync: jest.fn(),
  requestPermissionsAsync: jest.fn(),
  getExpoPushTokenAsync: jest.fn(),
  scheduleNotificationAsync: jest.fn(),
  cancelScheduledNotificationAsync: jest.fn(),
  AndroidImportance: { DEFAULT: 3 },
  SchedulableTriggerInputTypes: { DATE: 'date' },
}));
jest.mock('expo-device', () => ({ isDevice: false }));

describe('reminder offsets', () => {
  it('matches the offsets the database trigger schedules', () => {
    // sync_product_reminders() inserts rows at 30 and 7 days. If these drift,
    // the local fallback fires on different days than the server reminder.
    expect(REMINDER_OFFSET_DAYS).toEqual([30, 7]);
  });
});

describe('reminderTitle', () => {
  it('distinguishes the month-out nudge from the final one', () => {
    expect(reminderTitle(30)).toBe('Warranty expiring next month');
    expect(reminderTitle(7)).toBe('Warranty expiring soon');
  });
});

describe('reminderBody', () => {
  const product = {
    name: 'Sony WH-1000XM6',
    merchant: 'TechWorld',
    warranty_expires_at: '2028-03-14',
  };

  it('names the product, the store and the expiry date', () => {
    const body = reminderBody(product, 30);
    expect(body).toContain('Sony WH-1000XM6');
    expect(body).toContain('TechWorld');
    expect(body).toContain('2028-03-14');
    expect(body).toContain('in 30 days');
  });

  it('omits the store when the receipt did not name one', () => {
    const body = reminderBody({ ...product, merchant: null }, 7);
    expect(body).toContain('Sony WH-1000XM6');
    expect(body).not.toContain('from');
  });

  it('says "tomorrow" rather than "in 1 days"', () => {
    expect(reminderBody(product, 1)).toContain('tomorrow');
  });
});
