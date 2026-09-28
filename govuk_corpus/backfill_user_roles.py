"""One-time: set every non-break-glass account to the 'User' role.

Run ONCE when standing up the break-glass admin model — it demotes all existing accounts to the
general 'User' role, leaving the break-glass Admin untouched. Admins are then granted individually
via user management. This is deliberately NOT wired into `init_db`: it must not run on every
startup, or it would undo any Admin later granted through user management.

    set -a; . /home/ubuntu/gov-uk-corpus.env; set +a
    python3 -m govuk_corpus.backfill_user_roles --dry-run   # preview
    python3 -m govuk_corpus.backfill_user_roles             # apply
"""
from __future__ import annotations

import argparse

from . import accounts
from .backend import db


def backfill(conn, *, dry_run: bool = False) -> list:
    """Demote every account whose role isn't already 'User' (except the break-glass account).
    Returns the rows that were (or would be) changed."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, email, role FROM auth.users WHERE role <> 'User'").fetchall()]
    targets = [r for r in rows if not accounts.is_breakglass_email(r["email"])]
    if targets and not dry_run:
        for t in targets:
            conn.execute("UPDATE auth.users SET role='User', updated_at=now() WHERE id=%s", (t["id"],))
        conn.commit()
    return targets


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Set all non-break-glass accounts to the User role (one-time).")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    args = ap.parse_args()

    if not db.__name__.endswith("db_pg"):
        raise SystemExit("Accounts are Postgres-only — set DB_BACKEND=postgres + DB_* env.")
    conn = db.connect(None)
    targets = backfill(conn, dry_run=args.dry_run)
    verb = "Would demote" if args.dry_run else "Demoted"
    print(f"{verb} {len(targets)} account(s) to User:")
    for t in targets:
        print(f"  {t['email']}  ({t['role']} -> User)")
    if not targets:
        print("  (nothing to do — every non-break-glass account is already User)")
    conn.close()


if __name__ == "__main__":
    main()
