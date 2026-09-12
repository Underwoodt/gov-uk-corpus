"""First-session pilot: prove the run + canonicalise + hash + upsert loop.

Usage:
    python3 -m govuk_corpus.pilot                 # uses the built-in DEFRA seed frontier
    python3 -m govuk_corpus.pilot --db data/pilot.db
    python3 -m govuk_corpus.pilot --from-content-db ~/Downloads/content.db --limit 15

Run it twice: the first run reports `new`, the second reports `unchanged`
(the content hash matched), demonstrating change detection.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from typing import List

from . import config, db
from .stage_align import align_urls


def _seed_from_content_db(path: str, limit: int) -> List[str]:
    """Pull a small DEFRA-org slice from the existing content.db (read-only)."""
    uri = f"file:{os.path.expanduser(path)}?mode=ro&immutable=1"
    src = sqlite3.connect(uri, uri=True)
    like = "%environment-agency%"
    rows = src.execute(
        "SELECT url FROM content WHERE organisation_slugs LIKE ? AND status_code = 200 "
        "ORDER BY url LIMIT ?",
        (like, limit),
    ).fetchall()
    src.close()
    return [r[0] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="GOV.UK corpus pilot (Stage 1 align).")
    ap.add_argument("--db", default="data/pilot.db", help="pilot SQLite path")
    ap.add_argument("--from-content-db", help="seed frontier from an existing content.db (read-only)")
    ap.add_argument("--from-frontier", type=int, metavar="N",
                    help="align N URLs from the sitemap frontier (Stage 0 output)")
    ap.add_argument("--limit", type=int, default=15, help="seed size when using --from-content-db")
    ap.add_argument("--scope", default="defra-pilot")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)

    lastmods = None
    if args.from_frontier:
        rows = db.sitemap_frontier(conn, limit=args.from_frontier)
        seeds = [r["url"] for r in rows]
        lastmods = {r["url"]: r["lastmod"] for r in rows}
        source = "sitemap"
    elif args.from_content_db:
        seeds = _seed_from_content_db(args.from_content_db, args.limit)
        source = "sitemap"
    else:
        seeds = config.PILOT_SEED_URLS
        source = "seed"

    print(f"Seed frontier: {len(seeds)} URLs (source={source})")
    run_id = db.start_run(conn, stage="align", scope=args.scope)
    counters = align_urls(conn, run_id, seeds, source=source, lastmods=lastmods)
    db.finish_run(conn, run_id, counters)

    print(f"\nRun {run_id[:8]} complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:16} {v}")

    total = conn.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    orgs = conn.execute("SELECT COUNT(*) FROM page_organisations").fetchone()[0]
    print(f"\nCorpus now: {total} content rows, {orgs} organisation links.")
    print("Sample:")
    for row in conn.execute(
        "SELECT document_type, is_redirect, substr(title,1,60) t FROM content ORDER BY last_seen_at DESC LIMIT 6"
    ):
        flag = " [redirect]" if row["is_redirect"] else ""
        print(f"  - ({row['document_type']}){flag} {row['t']}")
    conn.close()


if __name__ == "__main__":
    main()
