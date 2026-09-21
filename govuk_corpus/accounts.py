"""User accounts + Argon2id password hashing — PostgreSQL only.

Auth lives in the `auth` schema (see schema_auth.sql). Registration is restricted
to the allowed email domains; passwords are hashed with Argon2id via argon2-cffi
using the library's recommended parameters (never hand-rolled, never plaintext,
never returned through the public API). Email uniqueness is case-insensitive.

Pure helpers (domain validation, hashing/verification) have no DB dependency and
are unit-tested directly; the DB helpers require the Postgres backend.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")

ALLOWED_DOMAINS = ("defra.gov.uk", "equalexperts.com")
DOMAIN_REJECT_MESSAGE = "Please use your DEFRA or Equal Experts email address."
ROLES = ("Admin", "Team Manager", "User", "Tester")
STATUSES = ("pending", "active", "disabled")

# Password policy. Best practice (NIST 800-63B): make length the primary control,
# accept a long passphrase, allow a generous character set, and reject only the
# characters that get mis-typed or paste-mangled (control / non-ASCII).
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128
_PASSWORD_ALLOWED = frozenset(chr(c) for c in range(0x20, 0x7F))   # printable ASCII: space..~

PASSWORD_RULES = (
    f"At least {MIN_PASSWORD_LEN} characters — longer is stronger. A short phrase of "
    "three random words is easiest to remember and hard to guess.",
    f"Up to {MAX_PASSWORD_LEN} characters.",
    "Letters, numbers, spaces and standard keyboard symbols only.",
    "No accented or non-English letters and no control characters.",
    "Don't reuse a password from another website.",
)

# A small, deliberately plain wordlist for the suggested passphrase. Every word is
# 5+ letters, so three of them plus separators always clear the 12-character minimum.
# Not security-sensitive on its own — Argon2 hashing and length carry the strength;
# it is only a starting suggestion the user can accept or replace.
_WORDS = (
    "amber", "anchor", "apple", "arrow", "aspen", "basil", "beacon", "birch", "bison",
    "bramble", "breeze", "cedar", "cider", "clover", "cobalt", "copper", "coral",
    "cotton", "crane", "delta", "ember", "falcon", "fern", "fjord", "forest", "garden",
    "ginger", "glacier", "granite", "harbour", "hazel", "heron", "indigo", "island",
    "jasper", "juniper", "kettle", "lantern", "ledger", "linen", "maple", "meadow",
    "mellow", "mirror", "moss", "nectar", "orchid", "otter", "pebble", "pewter",
    "pine", "quartz", "raven", "ribbon", "river", "saffron", "sable", "spruce",
    "stone", "sugar", "thistle", "timber", "topaz", "velvet", "walnut", "willow",
)


def suggest_passphrase(words: int = 3) -> str:
    """Return a memorable suggested passphrase: `words` random words joined by hyphens
    (lowercase letters + hyphens only, always over 12 characters). Uses `secrets` for
    unbiased random choice. A suggestion only — the user may type their own."""
    picks = [secrets.choice(_WORDS) for _ in range(max(1, words))]
    phrase = "-".join(picks)
    while len(phrase) <= 12:                       # guard against an unlucky short draw
        picks.append(secrets.choice(_WORDS))
        phrase = "-".join(picks)
    return phrase


def validate_password(password: str) -> None:
    """Raise ValueError with a user-facing message if the password fails policy.
    Length first, then the allowed-character check (see PASSWORD_RULES)."""
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    if len(password) > MAX_PASSWORD_LEN:
        raise ValueError(f"Password must be {MAX_PASSWORD_LEN} characters or fewer.")
    if any(ch not in _PASSWORD_ALLOWED for ch in password):
        raise ValueError("Password contains characters that aren't allowed. Use letters, "
                         "numbers, spaces and standard keyboard symbols only.")


_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema_auth.sql")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Columns safe to return through the app/API — never includes password_hash.
_PUBLIC_COLS = ("id, email, first_name, last_name, role, account_status, "
                "email_verified_at, failed_login_count, locked_until, "
                "created_at, updated_at, last_login_at, must_change_password")


class EmailTakenError(Exception):
    """Raised when an account already exists for the (case-insensitive) email."""


def _require_pg() -> None:
    if not _IS_PG:
        raise RuntimeError("accounts requires the Postgres backend — auth is Postgres-only.")


# ---- pure helpers (no DB) -------------------------------------------------

def normalise_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def email_domain_allowed(email: Optional[str]) -> bool:
    """True only for a well-formed address on an allowed domain (case-insensitive)."""
    e = normalise_email(email)
    if not _EMAIL_RE.match(e):
        return False
    return e.rsplit("@", 1)[-1] in ALLOWED_DOMAINS


def _hasher():
    from argon2 import PasswordHasher       # argon2-cffi; recommended defaults
    return PasswordHasher()


def hash_password(password: str) -> str:
    """Argon2id hash (unique salt generated by the library)."""
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    from argon2 import PasswordHasher
    from argon2.exceptions import Argon2Error
    try:
        return PasswordHasher().verify(password_hash, password)
    except (Argon2Error, ValueError, TypeError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash used weaker-than-current parameters (rehash on next login)."""
    try:
        return _hasher().check_needs_rehash(password_hash)
    except Exception:
        return True


# ---- DB helpers (Postgres) ------------------------------------------------

def init_auth_schema(conn) -> None:
    """Create the auth schema + tables (idempotent). Applied at deploy/startup."""
    _require_pg()
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        conn.execute(fh.read())
    conn.commit()


def create_user(conn, *, email: str, first_name: str, last_name: str, password: str,
                role: str = "User", account_status: str = "active") -> dict:
    """Create an account. `role`/`account_status` are explicit arguments (never taken
    from request data) to prevent mass-assignment. Returns the public row (no hash).
    Raises ValueError for validation problems, EmailTakenError for a duplicate email."""
    _require_pg()
    e = normalise_email(email)
    if not email_domain_allowed(e):
        raise ValueError(DOMAIN_REJECT_MESSAGE)
    if role not in ROLES:
        raise ValueError("invalid role")
    if account_status not in STATUSES:
        raise ValueError("invalid account status")
    if not (first_name or "").strip() or not (last_name or "").strip():
        raise ValueError("First and last name are required.")
    validate_password(password)
    pw = hash_password(password)
    try:
        row = conn.execute(
            f"INSERT INTO auth.users (email, first_name, last_name, password_hash, role, account_status) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_PUBLIC_COLS}",
            (e, first_name.strip(), last_name.strip(), pw, role, account_status)).fetchone()
        conn.commit()
        return dict(row)
    except Exception as exc:                    # unique-violation on lower(email)
        conn.rollback()
        if "users_email_lower_uniq" in str(exc) or "unique" in str(exc).lower():
            raise EmailTakenError(e) from None
        raise


def get_user_by_email(conn, email: str, *, with_hash: bool = False) -> Optional[dict]:
    """Fetch a user by case-insensitive email. `with_hash=True` (login only) also
    returns password_hash — never expose that to the client."""
    _require_pg()
    cols = _PUBLIC_COLS + (", password_hash" if with_hash else "")
    row = conn.execute(f"SELECT {cols} FROM auth.users WHERE lower(email) = lower(%s)",
                       (normalise_email(email),)).fetchone()
    return dict(row) if row else None


def get_user(conn, user_id: str, *, with_hash: bool = False) -> Optional[dict]:
    _require_pg()
    cols = _PUBLIC_COLS + (", password_hash" if with_hash else "")
    row = conn.execute(f"SELECT {cols} FROM auth.users WHERE id = %s", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users_query():
    """(sql, params) for the admin user list — so the page can show the SQL it ran."""
    return (f"SELECT {_PUBLIC_COLS} FROM auth.users ORDER BY created_at DESC", [])


def list_users(conn) -> List[dict]:
    """All accounts (public columns, no hashes), newest first — for the admin user list."""
    _require_pg()
    sql, params = list_users_query()
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def update_user(conn, user_id: str, *, first_name: Optional[str] = None,
                last_name: Optional[str] = None, role: Optional[str] = None,
                account_status: Optional[str] = None) -> Optional[dict]:
    """Update a user's profile fields. Fields are explicit arguments (never taken wholesale
    from request data) to prevent mass-assignment; role/status are validated. Returns the
    updated public row."""
    _require_pg()
    sets, params = [], []
    if first_name is not None:
        sets.append("first_name = %s"); params.append(first_name.strip())
    if last_name is not None:
        sets.append("last_name = %s"); params.append(last_name.strip())
    if role is not None:
        if role not in ROLES:
            raise ValueError("invalid role")
        sets.append("role = %s"); params.append(role)
    if account_status is not None:
        if account_status not in STATUSES:
            raise ValueError("invalid account status")
        sets.append("account_status = %s"); params.append(account_status)
    if not sets:
        return get_user(conn, user_id)
    sets.append("updated_at = %s"); params.append(datetime.now(timezone.utc).isoformat())
    params.append(user_id)
    conn.execute(f"UPDATE auth.users SET {', '.join(sets)} WHERE id = %s", tuple(params))
    conn.commit()
    return get_user(conn, user_id)


def change_password(conn, user_id: str, old_password: str, new_password: str) -> None:
    """Change a user's own password: verify the current password, validate the new one,
    then store the new Argon2id hash. Raises ValueError with a user-facing message if the
    current password is wrong or the new password fails policy."""
    _require_pg()
    user = get_user(conn, user_id, with_hash=True)
    if user is None:
        raise ValueError("Account not found.")
    if not verify_password(user["password_hash"], old_password):
        raise ValueError("Your current password is incorrect.")
    validate_password(new_password)
    pw = hash_password(new_password)
    conn.execute("UPDATE auth.users SET password_hash=%s, updated_at=now() WHERE id=%s",
                 (pw, user_id))
    conn.commit()


# ---- password reset (forgotten password) + forced change ------------------
RESET_TTL_HOURS = 24


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def set_password(conn, user_id: str, new_password: str) -> None:
    """Set a user's password WITHOUT the old one (reset-token or forced-change flows). Validates
    policy, stores the Argon2id hash, and clears the must-change flag."""
    _require_pg()
    validate_password(new_password)
    conn.execute("UPDATE auth.users SET password_hash=%s, must_change_password=false, "
                 "updated_at=now() WHERE id=%s", (hash_password(new_password), user_id))
    conn.commit()


def require_password_change(conn, user_id: str, on: bool = True) -> None:
    """Admin 'dirties' (or clears) a record so the user must set a new password at next login."""
    _require_pg()
    conn.execute("UPDATE auth.users SET must_change_password=%s, updated_at=now() WHERE id=%s",
                 (bool(on), user_id))
    conn.commit()


def create_reset_token(conn, user_id: str, ttl_hours: int = RESET_TTL_HOURS) -> str:
    """Mint a single-use reset token for a user. Stores only its hash; returns the raw token
    for the URL. Any earlier unused tokens for the user are invalidated first."""
    _require_pg()
    conn.execute("UPDATE auth.password_resets SET used_at=now() WHERE user_id=%s AND used_at IS NULL",
                 (user_id,))
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    conn.execute("INSERT INTO auth.password_resets (user_id, token_hash, expires_at) VALUES (%s,%s,%s)",
                 (user_id, _token_hash(token), expires))
    conn.commit()
    return token


def user_for_reset_token(conn, token: str) -> Optional[dict]:
    """The (public) user a valid, unused, unexpired token belongs to, else None."""
    _require_pg()
    if not token:
        return None
    cols = ", ".join("u." + c.strip() for c in _PUBLIC_COLS.split(","))   # qualify for the JOIN
    row = conn.execute(
        f"SELECT {cols} FROM auth.users u "
        f"JOIN auth.password_resets r ON r.user_id = u.id "
        f"WHERE r.token_hash=%s AND r.used_at IS NULL AND r.expires_at > now() LIMIT 1",
        (_token_hash(token),)).fetchone()
    return dict(row) if row else None


def reset_password_with_token(conn, token: str, new_password: str) -> bool:
    """Consume a valid token and set the new password. Returns True on success, False if the
    token is invalid/expired/used. Raises ValueError if the new password fails policy."""
    _require_pg()
    u = user_for_reset_token(conn, token)
    if not u:
        return False
    validate_password(new_password)                 # before consuming the token
    conn.execute("UPDATE auth.password_resets SET used_at=now() WHERE token_hash=%s", (_token_hash(token),))
    set_password(conn, u["id"], new_password)        # also clears must_change_password + commits
    return True


# ---- login: lockout, audit, authentication --------------------------------

MAX_FAILED_LOGINS = 5           # lock the account after this many consecutive failures
LOCK_MINUTES = 15               # temporary lock duration
# Enumeration-safe: show the same message whether the email is unknown or the
# password is wrong (never reveal which).
LOGIN_FAILED_MESSAGE = "Email address or password is incorrect."
LOGIN_LOCKED_MESSAGE = ("Too many failed attempts. Your account is temporarily locked — "
                        "try again later.")


def audit(conn, event: str, *, user_id=None, actor_id=None, ip=None, detail=None) -> None:
    """Append a security audit-log row. Never write secrets/passwords here."""
    _require_pg()
    conn.execute(
        "INSERT INTO auth.audit_log (event, user_id, actor_id, ip, detail) VALUES (%s,%s,%s,%s,%s)",
        (event, user_id, actor_id, ip, (detail or None)))
    conn.commit()


def _is_locked(user: dict) -> bool:
    lu = user.get("locked_until")
    if lu is None:
        return False
    if getattr(lu, "tzinfo", None) is None:
        lu = lu.replace(tzinfo=timezone.utc)
    return lu > datetime.now(timezone.utc)


def _record_failed(conn, user: dict, ip=None) -> None:
    n = (user.get("failed_login_count") or 0) + 1
    if n >= MAX_FAILED_LOGINS:
        conn.execute("UPDATE auth.users SET failed_login_count=%s, "
                     "locked_until = now() + make_interval(mins => %s), updated_at=now() WHERE id=%s",
                     (n, LOCK_MINUTES, user["id"]))
    else:
        conn.execute("UPDATE auth.users SET failed_login_count=%s, updated_at=now() WHERE id=%s",
                     (n, user["id"]))
    conn.commit()
    audit(conn, "login_failed", user_id=user["id"], ip=ip, detail=f"count={n}")


def authenticate(conn, email: str, password: str, *, ip=None):
    """Verify credentials. Returns (public_user, None) on success, or (None, reason)
    where reason is 'invalid' | 'inactive' | 'locked'. Enumeration-safe — the caller
    shows LOGIN_FAILED_MESSAGE for everything except 'locked'. Applies lockout and
    writes audit rows; never returns or logs the password hash."""
    _require_pg()
    user = get_user_by_email(conn, email, with_hash=True)
    if user is None:
        return None, "invalid"
    if user["account_status"] != "active":
        audit(conn, "login_denied", user_id=user["id"], ip=ip, detail=f"status={user['account_status']}")
        return None, "inactive"
    if _is_locked(user):
        audit(conn, "login_locked", user_id=user["id"], ip=ip)
        return None, "locked"
    if not verify_password(user["password_hash"], password):
        _record_failed(conn, user, ip)
        return None, "invalid"
    conn.execute("UPDATE auth.users SET failed_login_count=0, locked_until=NULL, "
                 "last_login_at=now(), updated_at=now() WHERE id=%s", (user["id"],))
    conn.commit()
    audit(conn, "login_succeeded", user_id=user["id"], ip=ip)
    user.pop("password_hash", None)
    return user, None
