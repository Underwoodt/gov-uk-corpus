"""Stage 3 — expand into linked child/attachment pages (into the ONE corpus).

For each page already in `content`, read its payload for child/attachment page
references (links.children, details.parts, inline html attachments), record the
parent->child relationship in `page_links`, and import any child page not already
in the corpus (source="attachment") by reusing Stage 1 align. This catches child
pages that are not in the sitemap. Non-page binaries (PDF/spreadsheets) are only
counted — deferred to a future binary_attachments table.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional

from . import config, db
from .extract import extract_child_links
from .stage_align import align_urls


def _new_counters() -> Dict[str, int]:
    return {k: 0 for k in ("parents", "child_links", "binaries", "children_to_import")}


def run_stage3(conn, run_id: str, limit: Optional[int] = None) -> Dict[str, int]:
    counters = _new_counters()
    q = "SELECT url, content FROM content WHERE content IS NOT NULL ORDER BY url"
    params: tuple = ()
    if limit:
        q += " LIMIT ?"
        params = (limit,)
    rows = conn.execute(q, params).fetchall()

    to_import: List[str] = []
    for row in rows:
        counters["parents"] += 1
        try:
            payload = json.loads(row["content"])
        except (ValueError, TypeError):
            continue
        children, binaries = extract_child_links(payload, row["url"])
        counters["binaries"] += binaries
        for child_url, relation in children:
            conn.execute(
                "INSERT OR IGNORE INTO page_links (parent_url, child_url, relation) VALUES (?,?,?)",
                (row["url"], child_url, relation),
            )
            counters["child_links"] += 1
            exists = conn.execute("SELECT 1 FROM content WHERE url=?", (child_url,)).fetchone()
            if not exists:
                to_import.append(child_url)
        conn.commit()

    to_import = list(dict.fromkeys(to_import))  # de-dupe, preserve order
    counters["children_to_import"] = len(to_import)
    if to_import:
        ic = align_urls(conn, run_id, to_import, source="attachment", stage="attachment")
        counters["imported_new"] = ic["new"]
        counters["imported_unchanged"] = ic["unchanged"]
        counters["imported_no_content_item"] = ic["no_content_item"]
        counters["imported_error"] = ic["error"]
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 3 — expand child/attachment pages.")
    ap.add_argument("--db", default="data/pilot.db")
    ap.add_argument("--limit", type=int, help="cap number of parent pages scanned")
    ap.add_argument("--scope", default="defra-pilot")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    run_id = db.start_run(conn, stage="attachment", scope=args.scope)
    counters = run_stage3(conn, run_id, limit=args.limit)
    db.finish_run(conn, run_id, counters)

    print(f"Stage 3 run {run_id[:8]} complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:26} {v}")
    total_links = conn.execute("SELECT COUNT(*) FROM page_links").fetchone()[0]
    total_content = conn.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    print(f"\npage_links: {total_links} | content rows: {total_content}")
    conn.close()


if __name__ == "__main__":
    main()
