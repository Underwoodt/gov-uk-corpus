"""Backfill content.reading_age + content.gds_english_score from search_text.

Streams rows that have body text (search_text) but no analysis yet, computes the
UK reading age (Flesch-Kincaid + 5) and the GDS plain-English issue count, and
writes both. Rows are marked done by gds_english_score being set (always an int,
even 0 for clean/empty text), so empty-body rows aren't re-scanned; reading_age
stays NULL when the text is too short to score.

Memory-safe keyset paging, mirroring build_search_text.

    set -a; . ~/gov-uk-corpus.env; set +a          # select Postgres
    python3 -m govuk_corpus.build_readability --limit 100000    # a chunk
    python3 -m govuk_corpus.build_readability                   # everything
"""
from __future__ import annotations

import argparse
import os
from typing import Dict, Optional

from .backend import db
from .readability import analyse

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"


def _batch(conn, after_url: str, size: int):
    """Next `size` rows with body text but no analysis yet (keyset paging)."""
    q = (f"SELECT url, search_text FROM content "
         f"WHERE search_text IS NOT NULL AND gds_english_score IS NULL "
         f"AND url > {_P} ORDER BY url LIMIT {int(size)}")
    return conn.execute(q, (after_url,)).fetchall()


def _write(conn, url: str, reading_age: Optional[float], gds: int, findings: str) -> None:
    conn.execute(
        f"UPDATE content SET reading_age = {_P}, gds_english_score = {_P}, "
        f"gds_findings = {_P} WHERE url = {_P}",
        (reading_age, gds, findings or None, url))


def build(conn, limit: Optional[int] = None, batch: int = 300, after: str = "") -> Dict[str, int]:
    """Idempotent, resumable backfill. Only rows still needing analysis
    (gds_english_score IS NULL) are selected, so re-running after a crash picks up
    where it left off. Per-row failures are isolated: a bad row is marked done with
    the error recorded in gds_findings, so the run continues and never loops on it.
    Commits every batch, so at most one un-committed batch is ever repeated.
    """
    counters = {"scanned": 0, "scored": 0, "too_short": 0, "errors": 0}
    try:
        while True:
            rows = _batch(conn, after, batch)
            if not rows:
                break
            for row in rows:
                counters["scanned"] += 1
                after = row["url"]
                try:
                    ra, gds, findings = analyse(row["search_text"] or "")
                    counters["scored" if ra is not None else "too_short"] += 1
                except Exception as e:  # poison row: mark done so we don't retry it forever
                    ra, gds, findings = None, 0, f"analysis error: {type(e).__name__}: {e}"
                    counters["errors"] += 1
                _write(conn, row["url"], ra, gds, findings)
            conn.commit()
            print(f"  ...{counters['scanned']} scanned / {counters['scored']} scored / "
                  f"{counters['errors']} errors (last: {after})", flush=True)
            if limit and counters["scanned"] >= limit:
                break
    except KeyboardInterrupt:
        conn.commit()   # keep the batch in progress; the rest resumes next run
        print("\nInterrupted — progress committed; re-run to continue.", flush=True)
    counters["last_url"] = after
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill reading_age + gds_english_score.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap rows this run")
    ap.add_argument("--after", default="", help="resume from a url (keyset cursor)")
    args = ap.parse_args()

    if args.db and not _IS_PG:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    counters = build(conn, limit=args.limit, after=args.after)

    print("Readability backfill complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:12} {v}")
    if counters.get("last_url"):
        print(f"\nResume the next chunk with:  --after '{counters['last_url']}'")
    conn.close()


if __name__ == "__main__":
    main()
