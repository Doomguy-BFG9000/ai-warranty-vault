# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- A shield app icon replaces Expo's template icons on iOS, Android (adaptive and
  themed) and the web, and the same shield replaces the 🛡️ emoji inside the app.
- Tab bar icons: Ionicons for Add, Search and Settings (filled when selected), in place
  of emoji.

## [1.0.0] — 2026-10-13

First public release.

### Receipts and the vault
- Email/password sign-in with route guards; sessions stored in the device's secure store.
- Capture a receipt with the camera or pick one from the library; images go to private,
  per-user Supabase Storage.
- Claude vision extraction in the `extract-receipt` Edge Function — product, merchant,
  price, purchase date and warranty term — shown on an editable review screen and saved
  only after the user confirms.
- The vault: products with expiry chips computed in SQL, plus detail, edit and delete.
- Reminders 30 and 7 days before cover ends: server push via an hourly `pg_cron` job and
  an Edge Function, with on-device notifications as a fallback.
- A freemium product cap enforced by the database (HTTP 402), with an upgrade path; users
  cannot grant themselves premium.

### Document assistant (`services/ai`)
- Attach manuals, warranty cards and service terms (PDF, text or scans) to a product.
  Indexing runs as durable, retryable jobs with crash recovery and content-hash
  idempotency.
- Hybrid retrieval — pgvector cosine search plus Postgres full-text and literal
  error-code matching, fused with Reciprocal Rank Fusion — running under Row Level
  Security, so the owner filter shapes the top-k rather than trimming it.
- A per-document cap in retrieval, so one long document cannot crowd out a second
  document that disagrees with it.
- Grounded answers with server-verified citations; abstention when the documents don't
  say; disagreements shown as conflicts instead of resolved. The answer's outcome is
  derived from what the server can verify, not from the model's own label.
- A LangGraph agent with two read-only tools, a bounded tool loop, a clarification
  interrupt, and human-approved service-request drafts with exactly-once approval;
  conversations survive a server restart.
- An evaluation harness on a synthetic corpus with labelled questions, comparing
  vector-only and hybrid retrieval.

### Quality and project
- Jest tests for the app, and pytest behaviour tests against real Postgres + pgvector
  with the shipped migrations: two-user isolation, idempotent indexing, citation
  checks, prompt injection, provider failure and restart recovery.
- `verify-*` scripts that exercise RLS, storage isolation, the freemium cap, the
  extraction boundary and reminder delivery against a live local stack.
- CI on every push and pull request; README, architecture docs, contributing guide,
  security policy and code of conduct.

### Fixed
- The review screen now closes after a successful save, and repeated taps no longer
  save the same product twice.

[Unreleased]: https://github.com/jaworskig00/ai-warranty-vault/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/jaworskig00/ai-warranty-vault/releases/tag/v1.0.0
