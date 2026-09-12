"""Stage 0 — acquire the frontier from the GOV.UK sitemap.

The index (https://www.gov.uk/sitemap.xml) points to sub-sitemaps; each sub-sitemap
lists <url><loc> (+ optional <lastmod>). We upsert every URL into `sitemap`,
tracking new / updated (lastmod changed) / unchanged. Stage 1 then reads the
frontier (`db.sitemap_frontier`) to fetch only new or changed pages.

Two sources:
  - offline: parse local files in ./sitemaps/ (already present in this repo) — for
    fast, network-free testing.
  - live: fetch the index and sub-sitemaps from gov.uk (rate-limited).

Usage:
    python3 -m govuk_corpus.stage_sitemap --db data/pilot.db --from-dir sitemaps
    python3 -m govuk_corpus.stage_sitemap --db data/pilot.db --live --limit-sitemaps 2
"""
from __future__ import annotations

import argparse
import glob
import os
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import httpx

from . import config, db
from .canonical import canonicalise

_SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _text(el, tag: str) -> Optional[str]:
    child = el.find(_SM_NS + tag)
    if child is None:  # tolerate un-namespaced sitemaps
        child = el.find(tag)
    return child.text.strip() if child is not None and child.text else None


def parse_index(xml_text: str) -> List[str]:
    """Return sub-sitemap URLs from a <sitemapindex>."""
    root = ET.fromstring(xml_text)
    return [loc for sm in root.iter() if sm.tag.endswith("sitemap")
            for loc in [_text(sm, "loc")] if loc]


def parse_urlset(xml_text: str) -> List[Tuple[str, Optional[str]]]:
    """Return (loc, lastmod) pairs from a <urlset>."""
    root = ET.fromstring(xml_text)
    out: List[Tuple[str, Optional[str]]] = []
    for url_el in root.iter(_SM_NS + "url"):
        loc = _text(url_el, "loc")
        if loc:
            out.append((loc, _text(url_el, "lastmod")))
    return out


def _new_counters() -> Dict[str, int]:
    return {k: 0 for k in ("sub_sitemaps", "urls_seen", "new", "updated", "unchanged", "invalid")}


def _ingest_urlset(conn, run_id: str, sitemap_file: str,
                   pairs: List[Tuple[str, Optional[str]]], counters: Dict[str, int]) -> None:
    for loc, lastmod in pairs:
        counters["urls_seen"] += 1
        url = canonicalise(loc)
        if url is None:
            counters["invalid"] += 1
            db.log_fetch(conn, run_id, "sitemap", str(loc), "invalid", error="failed canonicalisation")
            continue
        result = db.upsert_sitemap(conn, url, sitemap_file, lastmod)
        counters[result] += 1
    conn.commit()


def refresh_from_dir(conn, run_id: str, directory: str) -> Dict[str, int]:
    """Parse local sub-sitemap files (skips the index file itself)."""
    counters = _new_counters()
    paths = sorted(glob.glob(os.path.join(directory, "sitemap_*.xml")))
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            pairs = parse_urlset(fh.read())
        counters["sub_sitemaps"] += 1
        _ingest_urlset(conn, run_id, os.path.basename(path), pairs, counters)
    return counters


def refresh_live(conn, run_id: str, index_url: str = config.GOVUK_SITEMAP_INDEX,
                 limit_sitemaps: Optional[int] = None) -> Dict[str, int]:
    counters = _new_counters()
    headers = {"User-Agent": config.USER_AGENT}
    interval = 1.0 / config.RATE_LIMIT_PER_SEC if config.RATE_LIMIT_PER_SEC else 0.0
    with httpx.Client(timeout=config.REQUEST_TIMEOUT, follow_redirects=True, headers=headers) as client:
        index = client.get(index_url)
        index.raise_for_status()
        subs = parse_index(index.text)
        if limit_sitemaps:
            subs = subs[:limit_sitemaps]
        for sub in subs:
            time.sleep(interval)
            resp = client.get(sub)
            if resp.status_code != 200:
                db.log_fetch(conn, run_id, "sitemap", sub, "error", http_status=resp.status_code)
                continue
            counters["sub_sitemaps"] += 1
            _ingest_urlset(conn, run_id, os.path.basename(sub), parse_urlset(resp.text), counters)
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 0 — sitemap frontier refresh.")
    ap.add_argument("--db", default="data/pilot.db")
    ap.add_argument("--from-dir", help="parse local sub-sitemap files (offline)")
    ap.add_argument("--live", action="store_true", help="fetch the sitemap from gov.uk")
    ap.add_argument("--limit-sitemaps", type=int, help="cap sub-sitemaps (live only)")
    ap.add_argument("--scope", default="defra-pilot")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    run_id = db.start_run(conn, stage="sitemap", scope=args.scope)

    if args.live:
        counters = refresh_live(conn, run_id, limit_sitemaps=args.limit_sitemaps)
    else:
        directory = args.from_dir or "sitemaps"
        counters = refresh_from_dir(conn, run_id, directory)

    db.finish_run(conn, run_id, counters)

    print(f"Stage 0 run {run_id[:8]} complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:14} {v}")
    total = conn.execute("SELECT COUNT(*) FROM sitemap").fetchone()[0]
    frontier = len(db.sitemap_frontier(conn))
    print(f"\nsitemap table: {total} URLs | frontier needing (re)fetch: {frontier}")
    conn.close()


if __name__ == "__main__":
    main()
