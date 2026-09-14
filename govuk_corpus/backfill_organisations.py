"""Backfill `page_organisations` from stored `content` JSON.

Migration (migrate_sqlite) loaded the `content` rows but never populated
`page_organisations` — those links are only written by the crawl (stage_align).
Since the raw API payload is stored on each content row, we can re-derive the
organisations locally, no re-fetch needed.

Idempotent: each page's org rows are DELETEd and re-INSERTed, so this also
repairs any stale slugs (e.g. the nested-path fix). Memory-safe: rows are
streamed in small keyset batches.

    # env must select Postgres (DB_HOST set), e.g.
    set -a; . ~/gov-uk-corpus.env; set +a
    python3 -m govuk_corpus.backfill_organisations --limit 100000   # a chunk
    python3 -m govuk_corpus.backfill_organisations                  # everything
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, Optional

from .backend import db
from .extract import extract_organisations

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"


def _batch(conn, after_url: str, size: int):
    """Next `size` content rows with stored JSON, url > after_url (keyset paging)."""
    q = (f"SELECT url, content FROM content "
         f"WHERE content IS NOT NULL AND url > {_P} "
         f"ORDER BY url LIMIT {int(size)}")
    return conn.execute(q, (after_url,)).fetchall()


def build(conn, limit: Optional[int] = None, batch: int = 300,
          after: str = "") -> Dict[str, int]:
    counters = {"scanned": 0, "pages_with_orgs": 0, "org_links": 0, "bad_json": 0}
    while True:
        rows = _batch(conn, after, batch)
        if not rows:
            break
        for row in rows:
            counters["scanned"] += 1
            after = row["url"]
            try:
                payload = json.loads(row["content"])
            except (ValueError, TypeError):
                counters["bad_json"] += 1
                continue
            orgs = extract_organisations(payload)
            db.replace_page_organisations(conn, row["url"], orgs)
            if orgs:
                counters["pages_with_orgs"] += 1
                counters["org_links"] += len(orgs)
        conn.commit()
        print(f"  ...{counters['scanned']} scanned / "
              f"{counters['org_links']} links (last: {after})", flush=True)
        if limit and counters["scanned"] >= limit:
            break
    counters["last_url"] = after
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill page_organisations from stored content JSON.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap rows this run (for chunking)")
    ap.add_argument("--after", default="", help="resume from a url (keyset cursor)")
    args = ap.parse_args()

    if args.db and not _IS_PG:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    counters = build(conn, limit=args.limit, after=args.after)

    print("Backfill complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:16} {v}")
    if counters.get("last_url"):
        print(f"\nResume the next chunk with:  --after '{counters['last_url']}'")
    conn.close()


if __name__ == "__main__":
    main()
