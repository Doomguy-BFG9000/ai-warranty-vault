import type { Database } from '@/types/database';

/** Convenience aliases over the generated schema types. */
export type Tables = Database['public']['Tables'];

export type Profile = Tables['profiles']['Row'];
export type Product = Tables['products']['Row'];
export type Reminder = Tables['reminders']['Row'];

export type ExtractionStatus = Database['public']['Enums']['extraction_status'];

/**
 * `warranty_expires_at` is a GENERATED ALWAYS column: Postgres rejects any
 * attempt to write it. The Supabase type generator still lists it as writable,
 * so it is stripped here to make that a compile-time error rather than a
 * runtime one. Ownership and timestamps are also server-managed.
 */
export type ProductInsert = Omit<
  Tables['products']['Insert'],
  'warranty_expires_at' | 'created_at' | 'updated_at'
>;

export type ProductUpdate = Omit<
  Tables['products']['Update'],
  'warranty_expires_at' | 'user_id' | 'created_at' | 'updated_at'
>;

/**
 * `is_premium` is protected by a database trigger — only billing may change it.
 * Excluding it here keeps the client from attempting an update that would be
 * rejected with PT403.
 */
export type ProfileUpdate = Pick<Tables['profiles']['Update'], 'expo_push_token' | 'email'>;
