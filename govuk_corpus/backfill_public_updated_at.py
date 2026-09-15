"""Backfill content.public_updated_at from the stored content JSON.

Most rows were imported without public_updated_at set, so the audit dashboard's
freshness histogram dumps ~94% of the corpus into "Unknown". The date is present in
the content JSON, so this fills the column from it — preferring the true public
last-updated date and falling back to ``updated_at`` when that is all the page has.

Resumable: rows are selected while public_updated_at is empty, keyset paged by url,
committing each batch — so a crash/stop mid-run resumes cleanly (rows that already got
a date are non-empty and skipped). Rows whose JSON carries no usable date stay empty
(honestly "Unknown"); within a single run the url cursor moves past them, but a *fresh*
re-run re-checks that residual set (harmless — same result). So one full run fills
everything fillable; re-running only re-scans the dateless remainder.

    set -a; . ~/gov-uk-corpus.env; set +a
    python3 -m govuk_corpus.backfill_public_updated_at            # everything missing
    python3 -m govuk_corpus.backfill_public_updated_at --limit 100000
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, Optional

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

# Priority order: the true public last-updated date first; updated_at only as a fallback.
_DATE_KEYS = ("public_updated_at", "public_timestamp", "updated_at", "first_published_at")


def extract_date(content: Optional[str]) -> Optional[str]:
    """First non-empty date field from a content JSON string, by priority; None if none."""
    if not content:
        return None
    try:
        obj = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    for k in _DATE_KEYS:
        v = obj.get(k)
        if v:
            return str(v)
    return None


def _batch(conn, after_url: str, size: int):
    q = (f"SELECT url, content FROM content "
         f"WHERE (public_updated_at IS NULL OR public_updated_at = '') AND url > {_P} "
         f"ORDER BY url LIMIT {int(size)}")
    return conn.execute(q, (after_url,)).fetchall()


def _write(conn, url: str, value: Optional[str]) -> None:
    # '' marks "checked, no date" so the row is not re-scanned next run.
    conn.execute(f"UPDATE content SET public_updated_at = {_P} WHERE url = {_P}",
                 (value if value is not None else "", url))


def build(conn, limit: Optional[int] = None, batch: int = 500, after: str = "") -> Dict[str, int]:
    counters = {"scanned": 0, "dated": 0, "no_date": 0}
    try:
        while True:
            rows = _batch(conn, after, batch)
            if not rows:
                break
            for row in rows:
                counters["scanned"] += 1
                after = row["url"]
                value = extract_date(row["content"])
                counters["dated" if value else "no_date"] += 1
                _write(conn, row["url"], value)
            conn.commit()
            print(f"  ...{counters['scanned']} scanned / {counters['dated']} dated (last: {after})",
                  flush=True)
            if limit and counters["scanned"] >= limit:
                break
    except KeyboardInterrupt:
        conn.commit()
        print("\nInterrupted — progress committed; re-run to continue.", flush=True)
    counters["last_url"] = after
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill content.public_updated_at from content JSON.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap rows this run")
    ap.add_argument("--after", default="", help="resume from a url (keyset cursor)")
    args = ap.parse_args()

    if args.db and not _IS_PG:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    counters = build(conn, limit=args.limit, after=args.after)

    print("public_updated_at backfill complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:12} {v}")
    if counters.get("last_url"):
        print(f"\nResume the next chunk with:  --after '{counters['last_url']}'")
    conn.close()


if __name__ == "__main__":
    main()
