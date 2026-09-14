"""Migrate the `test-scopes-3` table from the local SQLite content.db into Postgres.

The old SQLite `content.db` no longer lives on the server, so run this ON THE MAC
(where ~/Downloads/content.db still exists) pointing at the remote Postgres via the
usual DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD env vars.

Source table `test-scopes-3(test-scope, url)` maps a scope label -> gov.uk URL.
URLs are canonicalised on the way in (https://gov.uk/... -> https://www.gov.uk/...)
so they join cleanly to `content.url`; the original string is kept as `raw_url`.

Target table (created if absent):
    test_scopes_3(test_scope text, url text, raw_url text,
                  PRIMARY KEY (test_scope, url))

By default the target is fully reloaded (a snapshot), so re-running is idempotent.

    # from the Mac, env pointing at the server's Postgres:
    set -a; . ~/gov-uk-corpus.env; set +a       # then override DB_HOST for remote:
    DB_HOST=<server-public-ip> python3 -m govuk_corpus.migrate_test_scopes \
        --sqlite ~/Downloads/content.db
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from typing import Optional

from .backend import db
from .canonical import canonicalise

TARGET_DDL = """
CREATE TABLE IF NOT EXISTS {t} (
    test_scope text NOT NULL,
    url        text NOT NULL,
    raw_url    text,
    PRIMARY KEY (test_scope, url)
)
"""


def _require_postgres() -> None:
    if not db.__name__.endswith("db_pg"):
        raise SystemExit(
            "Postgres backend not selected. Set DB_HOST (and DB_NAME/DB_USER/"
            "DB_PASSWORD) so this writes to Postgres, not local SQLite.\n"
            "  e.g.  set -a; . ~/gov-uk-corpus.env; set +a; "
            "DB_HOST=<server-ip> python3 -m govuk_corpus.migrate_test_scopes ..."
        )


def read_source(sqlite_path: str, table: str):
    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        rows = src.execute(f'SELECT "test-scope" AS scope, url FROM "{table}"').fetchall()
    finally:
        src.close()
    return rows


def migrate(sqlite_path: str, table: str, target: str,
            do_canonical: bool = True, replace: bool = True) -> dict:
    counters = {"read": 0, "loaded": 0, "skipped_bad_url": 0, "in_corpus": 0}
    rows = read_source(sqlite_path, table)
    counters["read"] = len(rows)

    conn = db.connect()
    conn.execute(TARGET_DDL.format(t=target))
    if replace:
        conn.execute(f"DELETE FROM {target}")

    for r in rows:
        raw = r["url"]
        url = canonicalise(raw) if do_canonical else raw
        if not url:
            counters["skipped_bad_url"] += 1
            continue
        conn.execute(
            f"INSERT INTO {target} (test_scope, url, raw_url) VALUES (%s, %s, %s) "
            f"ON CONFLICT (test_scope, url) DO UPDATE SET raw_url = EXCLUDED.raw_url",
            (r["scope"], url, raw),
        )
        counters["loaded"] += 1
    conn.commit()

    # coverage: how many scope URLs actually exist as usable corpus pages
    counters["in_corpus"] = conn.execute(
        f"SELECT COUNT(*) AS n FROM {target} ts "
        f"JOIN content c ON c.url = ts.url "
        f"WHERE c.is_redirect = 0 AND c.content_hash IS NOT NULL"
    ).fetchone()["n"]
    conn.close()
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate test-scopes-3 into Postgres.")
    ap.add_argument("--sqlite", default=os.path.expanduser("~/Downloads/content.db"),
                    help="source SQLite content.db (read-only)")
    ap.add_argument("--table", default="test-scopes-3", help="source table name")
    ap.add_argument("--target-table", default="test_scopes_3", help="Postgres table name")
    ap.add_argument("--no-canonical", action="store_true",
                    help="store URLs verbatim (do not normalise to www.gov.uk)")
    ap.add_argument("--append", action="store_true",
                    help="keep existing rows (default replaces the target snapshot)")
    args = ap.parse_args()

    _require_postgres()
    if not os.path.exists(args.sqlite):
        raise SystemExit(f"Source not found: {args.sqlite}")

    counters = migrate(args.sqlite, args.table, args.target_table,
                       do_canonical=not args.no_canonical, replace=not args.append)

    print(f"Migrated '{args.table}' -> Postgres '{args.target_table}'. Counters:")
    for k, v in counters.items():
        print(f"  {k:16} {v}")
    miss = counters["loaded"] - counters["in_corpus"]
    if miss:
        print(f"\nNote: {miss} scope URLs are not (yet) usable pages in `content` "
              f"(redirect, unfetched, or not in the corpus).")


if __name__ == "__main__":
    main()
