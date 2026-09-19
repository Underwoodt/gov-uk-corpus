"""Backfill `content.search_text` (HTML-stripped body) for keyword search.

One-time (resumable) pass over rows that have a stored `content` JSON but no
`search_text` yet. New pages get `search_text` written during the crawl (stage_align),
so this only fills the migrated/legacy backlog. On Postgres the generated `search_tsv`
recomputes automatically as each row's search_text is written.

    python3 -m govuk_corpus.build_search_text --limit 50000   # a chunk (run repeatedly)
    python3 -m govuk_corpus.build_search_text                 # remaining NULLs (long)
    python3 -m govuk_corpus.build_search_text --rescan        # recompute EVERY page (long)

search_text is the HTML-stripped body with the page's own organisation names removed, so a
keyword doesn't match a page merely because that organisation published it. Use --rescan to
re-apply the rules to pages already populated.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional

from .backend import db
from .text import search_text as _search_text

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"


def rows_needing_search_text(conn, limit: Optional[int] = None):
    """One page of rows still needing search_text (url + content). Memory-safe:
    call repeatedly with keyset pagination via `build`, not all at once."""
    q = ("SELECT url, content FROM content "
         "WHERE content IS NOT NULL AND search_text IS NULL "
         "ORDER BY url")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q).fetchall()


def _batch(conn, after_url: str, size: int, rescan: bool = False):
    """Next `size` rows with url > after_url (keyset paging). Normally only rows still needing
    search_text; with `rescan`, every row that has a stored payload (to recompute all)."""
    need = "" if rescan else "AND search_text IS NULL "
    q = (f"SELECT url, content FROM content "
         f"WHERE content IS NOT NULL {need}"
         f"AND url > {_P} ORDER BY url LIMIT {int(size)}")
    return conn.execute(q, (after_url,)).fetchall()


def build(conn, run_id: str, limit: Optional[int] = None, batch: int = 300,
          rescan: bool = False) -> Dict[str, int]:
    """Stream through rows in small keyset batches so memory stays bounded — only
    `batch` rows (with their content JSON) are held at a time. `rescan` recomputes every
    page's search_text (use after the extraction rules change, e.g. org-name stripping)."""
    counters = {"scanned": 0, "written": 0, "empty": 0, "bad_json": 0}
    after = ""
    while True:
        rows = _batch(conn, after, batch, rescan)
        if not rows:
            break
        for row in rows:
            counters["scanned"] += 1
            after = row["url"]
            try:
                payload = json.loads(row["content"])
            except (ValueError, TypeError):
                counters["bad_json"] += 1
                db.set_search_text(conn, row["url"], "")   # mark done so it isn't re-scanned
                continue
            text = _search_text(payload)   # HTML-stripped body with the page's own org names removed
            db.set_search_text(conn, row["url"], text)
            counters["written" if text else "empty"] += 1
        conn.commit()
        print(f"  ...{counters['scanned']} scanned / {counters['written']} written", flush=True)
        if limit and counters["scanned"] >= limit:
            break
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill content.search_text (body plain text).")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap rows this run")
    ap.add_argument("--scope", default="search-text")
    ap.add_argument("--rescan", action="store_true",
                    help="recompute search_text for EVERY page (not just NULLs) — use after the "
                         "extraction rules change, e.g. organisation-name stripping")
    args = ap.parse_args()

    if args.db:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    run_id = db.start_run(conn, stage="search_text", scope=args.scope)
    counters = build(conn, run_id, limit=args.limit, rescan=args.rescan)
    db.finish_run(conn, run_id, counters)

    print("Backfill complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:10} {v}")
    conn.close()


if __name__ == "__main__":
    main()
