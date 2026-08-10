/**
 * Contract between the extract-receipt Edge Function and the review screen.
 *
 * Every field is nullable: a crumpled or partially readable receipt should still
 * produce a usable draft rather than an error. The user confirms everything on
 * the review screen, so a wrong guess costs an edit, not a bad record.
 *
 * The JSON Schema these types mirror lives with the Edge Function, in
 * `supabase/functions/_shared/schema.ts` — it is only ever sent to Claude from
 * the server, so the app does not carry a second copy. Keep the two in step
 * when adding a field.
 */

export type ExtractionConfidence = 'high' | 'medium' | 'low';

export interface ReceiptExtraction {
  /** The purchased item, as specific as the receipt allows. */
  product_name: string | null;
  /** Store or retailer name. */
  merchant: string | null;
  /** ISO `YYYY-MM-DD`. Null when the date is absent or unreadable. */
  purchase_date: string | null;
  /** Item price in minor units, excluding tax where the receipt separates it. */
  price_cents: number | null;
  /** ISO 4217, inferred from the currency symbol or locale on the receipt. */
  currency: string | null;
  /** Warranty length in months, if the receipt states one. */
  warranty_months: number | null;
  /** Coarse product category, used for grouping in the vault. */
  category: string | null;
  /** How readable the receipt was overall — surfaced as a hint on the review screen. */
  confidence: ExtractionConfidence;
  /** Set when the image does not appear to be a receipt at all. */
  not_a_receipt: boolean;
}

export interface ExtractionResponse {
  extraction: ReceiptExtraction;
  usage?: { input_tokens: number; output_tokens: number };
}
