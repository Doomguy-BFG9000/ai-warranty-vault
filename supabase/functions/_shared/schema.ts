/**
 * JSON Schema handed to Claude's structured outputs, which constrains the model's
 * response to exactly this shape — no prompt-based "please return JSON", and no
 * parsing repair on the way back.
 *
 * This is the single source of truth for the extraction shape. The app's
 * matching TypeScript interface lives in `src/types/extraction.ts`; keep the two
 * in step when adding a field.
 *
 * Structured outputs require `additionalProperties: false` on every object and
 * do not accept a type array for nullables, so optional fields use `anyOf`.
 */
export const RECEIPT_EXTRACTION_SCHEMA = {
  type: 'object',
  properties: {
    product_name: {
      anyOf: [{ type: 'string' }, { type: 'null' }],
      description:
        'The specific product purchased, e.g. "Sony WH-1000XM6 Wireless Headphones". ' +
        'Null if no product line item is legible.',
    },
    merchant: {
      anyOf: [{ type: 'string' }, { type: 'null' }],
      description: 'The store name, e.g. "TechWorld Electronics".',
    },
    purchase_date: {
      anyOf: [{ type: 'string', format: 'date' }, { type: 'null' }],
      description:
        'Transaction date as YYYY-MM-DD. Convert any printed format to ISO. ' +
        'Null if no date is legible.',
    },
    price_cents: {
      anyOf: [{ type: 'integer' }, { type: 'null' }],
      description:
        "Price of the product itself in minor units (cents). Prefer the item's line " +
        'price over the order total, so tax and unrelated items are excluded. 429.99 -> 42999.',
    },
    currency: {
      anyOf: [{ type: 'string' }, { type: 'null' }],
      description: 'ISO 4217 code inferred from the receipt, e.g. "USD", "EUR", "PLN".',
    },
    warranty_months: {
      anyOf: [{ type: 'integer' }, { type: 'null' }],
      description:
        'Warranty length in months if the receipt states one ("24-month warranty" -> 24, ' +
        '"2 year guarantee" -> 24). Null when the receipt does not mention a warranty — ' +
        'do not infer one from the product type.',
    },
    category: {
      anyOf: [
        {
          type: 'string',
          enum: [
            'electronics',
            'appliance',
            'furniture',
            'tools',
            'vehicle',
            'clothing',
            'other',
          ],
        },
        { type: 'null' },
      ],
      description: 'Coarse category for the product.',
    },
    confidence: {
      type: 'string',
      enum: ['high', 'medium', 'low'],
      description:
        'Overall legibility of the receipt: high when fields were read cleanly, ' +
        'low when the image was blurry, cropped, or largely unreadable.',
    },
    not_a_receipt: {
      type: 'boolean',
      description:
        'True when the image is clearly not a purchase receipt or invoice, so the app ' +
        'can say so rather than saving an empty product.',
    },
  },
  required: [
    'product_name',
    'merchant',
    'purchase_date',
    'price_cents',
    'currency',
    'warranty_months',
    'category',
    'confidence',
    'not_a_receipt',
  ],
  additionalProperties: false,
} as const;
