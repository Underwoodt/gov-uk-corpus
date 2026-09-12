"""Run the full corpus cycle: Stage 0 -> 1 -> 2 -> 3, each as its own recorded run.

Backend is chosen by env (see govuk_corpus.backend): Postgres when DB_HOST/DB_BACKEND
is set, else SQLite. This is the single entry point for the daily cron job.

Examples:
    # server (Postgres via env), full cycle
    python3 -m govuk_corpus.run_all

    # local smoke test (SQLite), offline sitemap + small align cap
    python3 -m govuk_corpus.run_all --db data/pilot.db --sitemap-dir sitemaps --limit 5
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

from .backend import db
from .stage_align import align_urls
from .stage_attachments import run_stage3
from .stage_redirects import run_stage2
from .stage_sitemap import refresh_from_dir, refresh_live


def _print(stage: str, run_id: str, counters: dict) -> None:
    print(f"\n[{stage}] run {run_id[:8]}: " + ", ".join(f"{k}={v}" for k, v in counters.items()))


def run_cycle(conn, scope: str = "whole-govuk", *, sitemap_dir: Optional[str] = None,
              sitemaps_limit: Optional[int] = None, align_limit: Optional[int] = None) -> None:
    # Stage 0 — frontier
    r0 = db.start_run(conn, stage="sitemap", scope=scope)
    c0 = (refresh_from_dir(conn, r0, sitemap_dir) if sitemap_dir
          else refresh_live(conn, r0, limit_sitemaps=sitemaps_limit))
    db.finish_run(conn, r0, c0)
    _print("Stage 0 sitemap", r0, c0)

    # Stage 1 — align the frontier
    rows = db.sitemap_frontier(conn, limit=align_limit)
    urls = [r["url"] for r in rows]
    lastmods = {r["url"]: r["lastmod"] for r in rows}
    r1 = db.start_run(conn, stage="align", scope=scope)
    c1 = align_urls(conn, r1, urls, source="sitemap", lastmods=lastmods)
    db.finish_run(conn, r1, c1)
    _print("Stage 1 align", r1, c1)

    # Stage 2 — redirects
    r2 = db.start_run(conn, stage="redirect", scope=scope)
    c2 = run_stage2(conn, r2)
    db.finish_run(conn, r2, c2)
    _print("Stage 2 redirects", r2, c2)

    # Stage 3 — child/attachment expansion
    r3 = db.start_run(conn, stage="attachment", scope=scope)
    c3 = run_stage3(conn, r3)
    db.finish_run(conn, r3, c3)
    _print("Stage 3 attachments", r3, c3)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the full corpus cycle (Stage 0-3).")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--scope", default="whole-govuk")
    ap.add_argument("--sitemap-dir", help="Stage 0 offline: parse local sub-sitemaps")
    ap.add_argument("--sitemaps-limit", type=int, help="Stage 0 live: cap sub-sitemaps")
    ap.add_argument("--limit", type=int, help="Stage 1: cap frontier URLs aligned")
    args = ap.parse_args()

    if args.db:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    run_cycle(conn, scope=args.scope, sitemap_dir=args.sitemap_dir,
              sitemaps_limit=args.sitemaps_limit, align_limit=args.limit)
    print("\nCycle complete.")
    conn.close()


if __name__ == "__main__":
    main()
