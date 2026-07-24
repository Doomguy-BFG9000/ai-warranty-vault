# Product Plan — AI Warranty Vault

## Problem

Warranty proof is scattered across paper receipts, email confirmations, and product
boxes. When something breaks, people either can't find the receipt or have forgotten
the warranty already expired — so valid claims go unmade and money is lost.

## Solution

A mobile-first vault where a **single photo** becomes a tracked warranty. AI reads the
receipt so users don't type anything, and proactive reminders make sure coverage is
actually used.

## Target users

| Segment            | Pain                                                      |
| ------------------ | -------------------------------------------------------- |
| Electronics owners | Phones, laptops, appliances with 1–3 year warranties     |
| Homeowners         | Appliances, tools, furniture, home systems               |
| Families           | Many purchases across many people; nobody tracks them    |

## Core value loop

```
Snap receipt → AI extracts product + warranty → vault tracks expiry
      → reminder before expiry → user keeps/claims warranty → trust → more receipts
```

The AI-extraction step is the moat: it removes the manual data entry that makes every
"receipt organizer" app fail at adoption.

## MVP scope (must-have)

1. Email + password auth.
2. Capture / upload a receipt photo, stored securely per user.
3. AI extraction → **editable** structured fields (merchant, product, purchase date,
   price, warranty length, computed expiry). AI assists; the user confirms.
4. Product list + detail with expiry status: **active / expiring soon / expired**.
5. Reminders (push + local fallback) ahead of expiry.
6. Free-tier limit (5 products) with an upgrade prompt; `is_premium` flag bypasses it.

## Out of scope for MVP (roadmap)

- Real payment processing / subscription billing (RevenueCat).
- Importing receipts from email / Gmail.
- Family / shared vaults.
- Warranty-claim assistance workflow.
- Web app.

## Monetization

Freemium:

- **Free:** up to 5 tracked products, AI extraction, reminders.
- **Premium:** unlimited products (and future: family sharing, email import,
  warranty-claim help). Billing wired via RevenueCat in a later phase; the schema and
  gating are built now behind an `is_premium` flag.

## Success metrics

- **Activation (north star):** % of new users who add ≥1 product via photo in the first
  session.
- **Retention proxy:** reminders delivered and opened.
- **Monetization proxy:** % of active users who hit the free-tier limit.

## Fastest path to first real users

Ship the capture → extract → track → remind loop to TestFlight / Play internal testing.
The loop is demoable with a single receipt, so we can put it in front of real users the
moment Phases 0–6 land — before billing exists.
