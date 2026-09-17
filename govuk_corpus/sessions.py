"""Server-side sessions for the accounts login (PostgreSQL only).

A session is a row in ``auth.user_sessions``. The cookie value is ``<id>:<secret>``
where ``id`` is the row's uuid and ``secret`` is a high-entropy random token; only the
**SHA-256 of the secret** is stored (``token_hash``), so a database read can never yield
a usable session cookie. Because sessions are server-side, logout, rotation and
password-reset can truly invalidate them.

Validation enforces: matching secret, not revoked, not past absolute expiry, not idle
beyond the inactivity window, and the owning account still ``active``. ``create_session``
always mints a fresh row (session-fixation safe — never reuse a pre-login id).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Optional

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")

SESSION_TTL_HOURS = 12       # absolute lifetime
IDLE_MINUTES = 120           # inactivity timeout (rolling, via last_seen_at)
COOKIE_NAME = "sb_session"

# Columns returned for the logged-in user (never password_hash).
_USER_COLS = "u.id, u.email, u.first_name, u.last_name, u.role, u.account_status"


def _require_pg() -> None:
    if not _IS_PG:
        raise RuntimeError("sessions requires the Postgres backend — auth is Postgres-only.")


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def create_session(conn, user_id: str, *, ip: Optional[str] = None,
                   user_agent: Optional[str] = None, ttl_hours: int = SESSION_TTL_HOURS) -> str:
    """Mint a new session for a user and return the cookie value ``<id>:<secret>``."""
    _require_pg()
    secret = secrets.token_urlsafe(32)
    row = conn.execute(
        "INSERT INTO auth.user_sessions (user_id, token_hash, expires_at, ip, user_agent) "
        "VALUES (%s, %s, now() + make_interval(hours => %s), %s, %s) RETURNING id",
        (user_id, _hash(secret), int(ttl_hours), ip, (user_agent or "")[:400])).fetchone()
    conn.commit()
    return f"{row['id']}:{secret}"


def resolve(conn, cookie_value: Optional[str], *, idle_minutes: int = IDLE_MINUTES,
            touch: bool = True) -> Optional[dict]:
    """Return the logged-in user (public columns) for a valid cookie, else None.
    Refreshes last_seen_at when valid (unless touch=False)."""
    _require_pg()
    if not cookie_value or ":" not in cookie_value:
        return None
    sid, secret = cookie_value.split(":", 1)
    try:
        row = conn.execute(
            f"SELECT s.id AS sid, s.token_hash, s.expires_at, s.revoked_at, s.last_seen_at, "
            f"{_USER_COLS} FROM auth.user_sessions s JOIN auth.users u ON u.id = s.user_id "
            f"WHERE s.id = %s", (sid,)).fetchone()
    except Exception:            # malformed uuid etc. — treat as no session
        conn.rollback()
        return None
    if row is None:
        return None
    r = dict(row)
    if not r.get("token_hash") or not hmac.compare_digest(r["token_hash"], _hash(secret)):
        return None
    now = _now()
    if r["revoked_at"] is not None:
        return None
    if _aware(r["expires_at"]) <= now:
        return None
    last_seen = _aware(r["last_seen_at"])
    if last_seen is not None and (now - last_seen).total_seconds() > idle_minutes * 60:
        return None
    if r["account_status"] != "active":
        return None
    if touch:
        conn.execute("UPDATE auth.user_sessions SET last_seen_at = now() WHERE id = %s", (r["sid"],))
        conn.commit()
    return {"id": r["id"], "email": r["email"], "first_name": r["first_name"],
            "last_name": r["last_name"], "role": r["role"], "account_status": r["account_status"]}


def revoke(conn, cookie_value: Optional[str]) -> None:
    """Invalidate one session (logout). Safe to call with a bad/None cookie."""
    _require_pg()
    if not cookie_value or ":" not in cookie_value:
        return
    sid = cookie_value.split(":", 1)[0]
    try:
        conn.execute("UPDATE auth.user_sessions SET revoked_at = now() "
                     "WHERE id = %s AND revoked_at IS NULL", (sid,))
        conn.commit()
    except Exception:
        conn.rollback()


def revoke_all_for_user(conn, user_id: str) -> None:
    """Invalidate every session for a user (password reset / account disabled)."""
    _require_pg()
    conn.execute("UPDATE auth.user_sessions SET revoked_at = now() "
                 "WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
    conn.commit()
