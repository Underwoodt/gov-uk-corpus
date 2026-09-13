"""Backfill `content.search_text` (HTML-stripped body) for keyword search.

One-time (resumable) pass over rows that have a stored `content` JSON but no
`search_text` yet. New pages get `search_text` written during the crawl (stage_align),
so this only fills the migrated/legacy backlog. On Postgres the generated `search_tsv`
recomputes automatically as each row's search_text is written.

    python3 -m govuk_corpus.build_search_text --limit 50000   # a chunk (run repeatedly)
    python3 -m govuk_corpus.build_search_text                 # everything (long)
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional

from .backend import db
from .text import body_text

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"


def rows_needing_search_text(conn, limit: Optional[int] = None):
    q = ("SELECT url, content FROM content "
         "WHERE content IS NOT NULL AND (search_text IS NULL OR search_text = '') "
         "ORDER BY url")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q).fetchall()


def build(conn, run_id: str, limit: Optional[int] = None, batch: int = 1000) -> Dict[str, int]:
    counters = {"scanned": 0, "written": 0, "empty": 0, "bad_json": 0}
    rows = rows_needing_search_text(conn, limit=limit)
    pending = 0
    for row in rows:
        counters["scanned"] += 1
        try:
            payload = json.loads(row["content"])
        except (ValueError, TypeError):
            counters["bad_json"] += 1
            continue
        text = body_text(payload)
        db.set_search_text(conn, row["url"], text)
        counters["written" if text else "empty"] += 1
        pending += 1
        if pending >= batch:
            conn.commit()
            pending = 0
            print(f"  ...{counters['written']} written", flush=True)
    conn.commit()
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill content.search_text (body plain text).")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap rows this run")
    ap.add_argument("--scope", default="search-text")
    args = ap.parse_args()

    if args.db:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    run_id = db.start_run(conn, stage="search_text", scope=args.scope)
    counters = build(conn, run_id, limit=args.limit)
    db.finish_run(conn, run_id, counters)

    print("Backfill complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:10} {v}")
    conn.close()


if __name__ == "__main__":
    main()
