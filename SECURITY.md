# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately through GitHub: on this repository's **Security** tab, choose
**Report a vulnerability**. Include what you found, how to reproduce it, and what an
attacker could do with it. You can expect an acknowledgement within a few days.

## Scope

Most interesting here:

- **Cross-user data access** — anything that lets one user read, change or delete
  another user's products, receipts, documents, chunks, threads or claim drafts,
  including through vector search or the document-assistant API.
- **Authorization bypass** — granting yourself premium, skipping the free-tier cap, or
  approving a claim draft without the owner's request.
- **Secret exposure** — a server-side key reachable from the app bundle or a response.
- **Prompt injection with an effect** — document text that causes a tool call, a write,
  or data from another user to appear.

## Not vulnerabilities

- The JWTs in `scripts/verify-*.mjs` are Supabase's **public local-development demo
  keys** (`iss: supabase-demo`). Every `supabase start` ships the same values and they
  only work against a local stack.
- `EXPO_PUBLIC_SUPABASE_ANON_KEY` is designed to be public; Row Level Security enforces
  access.
- Model answers that are wrong but cite nothing they shouldn't, and don't cross a user
  boundary, are quality bugs — please open a normal issue.

## Supported versions

Only the latest `main` is maintained.
