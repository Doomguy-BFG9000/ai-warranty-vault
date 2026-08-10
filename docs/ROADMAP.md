# Roadmap — AI Warranty Vault

Each phase is a coherent, shippable slice with conventional commits. The MVP is
demoable (capture → extract → track → remind) by the end of Phase 6.

| Phase | Goal | Status |
| ----- | ---- | ------ |
| 0 | **Scaffold & repo** — Expo + Router, deps, docs, CLAUDE.md, GitHub repo | ✅ Done |
| 1 | **Backend foundation** — Supabase migrations (tables + RLS), `receipts` bucket, typed client | ✅ Done |
| 2 | **Auth** — email/pw sign up / in / out, session persistence, auth-gated routing, profile bootstrap | ✅ Done |
| 3 | **Capture & store** — camera / gallery, downsize, upload to Storage, create pending product | ✅ Done |
| 4 | **AI extraction** — `extract-receipt` Edge Function (Claude vision), editable review screen, compute expiry | ✅ Done |
| 5 | **Vault UX** — product list w/ expiry chips, detail, edit / delete, empty state | ⬜ |
| 6 | **Reminders** — push token registration, reminder rows, `send-reminders` + pg_cron, local fallback | ⬜ |
| 7 | **Freemium gating** — free product-count limit, upgrade sheet, `is_premium` bypass | ⬜ |
| 8 | **Polish & verify** — loading/error states, validation, README run steps, end-to-end simulator pass, `tsc` + lint | ⬜ |

## Post-MVP (later)

- RevenueCat billing for real premium subscriptions (App Store / Play).
- Email / Gmail receipt import.
- Family / shared vaults.
- Warranty-claim assistance (auto-fill claim forms, store serial numbers & photos).
- Web app via Expo web export.
- Categories, search, and filtering at scale.

## Verification per phase

- **Types/lint:** `npm run typecheck` and `npm run lint` stay green.
- **Backend:** migrations apply cleanly; RLS blocks cross-user reads; functions invoked
  with test payloads return expected shapes.
- **App:** end-to-end walkthrough in the iOS Simulator — sign up → snap receipt → confirm
  extracted fields → see it in the vault with an expiry chip → reminder row created.
