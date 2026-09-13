"""Reconciliation job — re-verify pages we already hold, to self-heal drift.

The incremental frontier only re-fetches when the sitemap `lastmod` advances. That
catches future changes, but not pages whose stored body predates a change we never
saw (e.g. legacy migrated content). This job re-fetches pages we have, re-hashes,
and updates only what actually changed — preserving each page's original `source`.

It processes the **stalest pages first** (oldest `last_seen_at`), and re-verifying a
page updates its `last_seen_at` to now — so repeated capped runs naturally cycle
through the whole corpus without repeating work:

    # one-off, on the server (Postgres via env)
    python3 -m govuk_corpus.reconcile --limit 50000     # ~stalest 50k; run daily to cycle
    python3 -m govuk_corpus.reconcile                   # whole corpus (long)
"""
from __future__ import annotations

import argparse
import os
from typing import List, Optional

from .backend import db
from .stage_align import align_urls


def urls_to_reverify(conn, limit: Optional[int] = None) -> List[str]:
    """Pages we have a body for, stalest first (oldest last_seen_at)."""
    q = ("SELECT url FROM content "
         "WHERE content_hash IS NOT NULL "
         "ORDER BY last_seen_at ASC")
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r["url"] for r in conn.execute(q).fetchall()]


def reconcile(conn, run_id: str, limit: Optional[int] = None) -> dict:
    urls = urls_to_reverify(conn, limit=limit)
    # source=None -> keep each page's existing source; re-fetch/hash/update in place.
    return align_urls(conn, run_id, urls, source=None, stage="reconcile")


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-verify held pages (self-heal drift).")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap pages this run (stalest first)")
    ap.add_argument("--scope", default="reconcile")
    args = ap.parse_args()

    if args.db:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    run_id = db.start_run(conn, stage="reconcile", scope=args.scope)
    counters = reconcile(conn, run_id, limit=args.limit)
    db.finish_run(conn, run_id, counters)

    print(f"Reconcile run {run_id[:8]} complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:16} {v}")
    conn.close()


if __name__ == "__main__":
    main()
