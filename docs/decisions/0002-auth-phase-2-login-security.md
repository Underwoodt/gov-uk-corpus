# 0002 — Auth phase 2: login security, rolled out behind a flag

- **Status:** accepted (2026-09-17)
- **Plan:** `.claude/plans/streamed-inventing-seal.md`; builds on Phase 1 (auth schema + `accounts.py`).

## Context

Phase 1 added the `auth` schema (`users`, `user_sessions`, `audit_log`) and
`accounts.py` (argon2id hashing, user creation, domain allow-list) — all dormant. The
live app still gates every route with a single shared `DASHBOARD_PASSWORD` + `authed()`.
Phase 2 introduces real per-user login/sessions, which ultimately replaces `authed()` on
every route — the widest, riskiest change in the programme.

## Decisions

1. **Parallel, behind a flag — no hard cutover.** `AUTH_MODE` env var: `shared`
   (default; existing shared-password gate, live behaviour unchanged) vs `accounts`
   (new per-user session login). Code can merge to `main` and deploy with zero live
   change until the flag is deliberately flipped.
2. **Postgres-only, isolated `auth` schema** for all account/session data; tested against
   the cloud test DB's `auth` schema. Never DDL/write the real corpus tables.
3. **Default-deny when `authed()` is replaced.** One central `require_login` /
   `current_user` dependency, plus a test asserting every non-public route is gated, so a
   forgotten route can't silently become open.
4. **Postgres-backed test path** for the auth/session/threat tests (the main suite is
   SQLite); corpus tests stay on SQLite.
5. **Security headers now (safe subset).** `X-Content-Type-Options`, `Referrer-Policy`,
   `X-Frame-Options`. **HSTS is opt-in** (`ENABLE_HSTS=1`) — only over confirmed HTTPS.
6. **CSP and CSRF are deferred, not first.** The app is full of inline scripts/styles and
   the POST forms have no CSRF tokens, so a strict CSP or CSRF enforcement now would break
   it. CSRF tokens land *with* the auth flip; CSP tightening is a hardening step.
7. **HTTPS at the edge is a go-live prerequisite** for `AUTH_MODE=accounts` (Secure
   cookies + HSTS). Not yet confirmed on the live server; local dev works over HTTP.

## Consequences

- Every merge in this phase is low-risk: nothing changes for live users until the flag
  flips. The riskiest step (replacing `authed()`) is guarded by default-deny + a coverage
  test.
- Going live with accounts is blocked on: HTTPS/nginx (for Secure/HSTS), the least-priv DB
  role, and the threat-test acceptance gates (session fixation, enumeration-safe errors,
  rate-limit/lockout, CSRF).

## Increment order

1. Foundations — security headers + `AUTH_MODE` scaffold (this PR). *No behaviour change.*
2. `sessions.py` — server-side sessions on `auth.user_sessions` (PG-tested).
3. Login/logout with sessions + `current_user`, behind `AUTH_MODE=accounts`.
4. Replace `authed()` everywhere + default-deny coverage test.

Registration, RBAC, and the admin area follow as later phases.
