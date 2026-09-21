-- User accounts / authentication — PostgreSQL only.
--
-- Isolated in the `auth` schema so it never collides with the corpus tables in
-- `public`. No extensions required: UUIDs use the built-in gen_random_uuid()
-- (PG13+) and case-insensitive email uniqueness uses a lower(email) index rather
-- than CITEXT (so the least-privilege app role needs no extension rights).
--
-- There is deliberately NO plaintext password column — only an Argon2id hash.

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email               text NOT NULL,
    first_name          text NOT NULL,
    last_name           text NOT NULL,
    password_hash       text NOT NULL,            -- Argon2id (argon2-cffi); never plaintext
    role                text NOT NULL DEFAULT 'User'
                        CHECK (role IN ('Admin', 'Team Manager', 'User', 'Tester')),
    account_status      text NOT NULL DEFAULT 'active'
                        CHECK (account_status IN ('pending', 'active', 'disabled')),
    email_verified_at   timestamptz,               -- nullable; email verification wired later
    failed_login_count  integer NOT NULL DEFAULT 0,
    locked_until        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    last_login_at       timestamptz
);
-- Case-insensitive unique email (replaces CITEXT).
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_uniq ON auth.users (lower(email));
-- Force a password change on next login (admin "dirties" the record). Additive for existing DBs.
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;

-- One-time password-reset links. The URL carries a random token; we only store its SHA-256
-- (like sessions), so a DB read can't mint a working link. Single-use (used_at) and expiring.
-- Iteration 1: the link is shown on screen (self-serve) or handed over by an admin; a later
-- iteration emails it instead.
CREATE TABLE IF NOT EXISTS auth.password_resets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    token_hash  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);
CREATE INDEX IF NOT EXISTS password_resets_user ON auth.password_resets (user_id);
CREATE INDEX IF NOT EXISTS password_resets_token ON auth.password_resets (token_hash);

-- Server-side sessions, so logout / rotation / password-reset can truly invalidate.
CREATE TABLE IF NOT EXISTS auth.user_sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    token_hash    text NOT NULL,                     -- SHA-256 of the cookie secret; never the secret itself
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz,
    ip            text,
    user_agent    text
);
CREATE INDEX IF NOT EXISTS user_sessions_user ON auth.user_sessions (user_id);
-- Additive for any pre-existing (Phase-1) sessions table.
ALTER TABLE auth.user_sessions ADD COLUMN IF NOT EXISTS token_hash text;

-- Security audit log (no secrets are ever written here).
CREATE TABLE IF NOT EXISTS auth.audit_log (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    event     text NOT NULL,                       -- e.g. account_created, login_succeeded
    user_id   uuid REFERENCES auth.users(id) ON DELETE SET NULL,   -- subject
    actor_id  uuid REFERENCES auth.users(id) ON DELETE SET NULL,   -- who did it (e.g. an admin)
    ip        text,
    detail    text
);
CREATE INDEX IF NOT EXISTS audit_log_at ON auth.audit_log (at);
