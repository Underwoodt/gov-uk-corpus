"""Backfill the plain-English audit columns on `content` from search_text.

For each row with body text it computes the UK reading age and runs the class-based
GDS scan (readability.scan), writing:
  reading_age, gds_english_score (weighted impact), gds_findings (class summary),
  gds_checks (JSON {"words":N,"counts":{class:count}}), gds_stars (1–5).

Rows are marked done by gds_english_score being set (always a number, even 0.0 for
clean/empty text). Use --rescan to re-process every row (needed after the checks or
weights change). Memory-safe keyset paging, mirroring build_search_text.

    set -a; . ~/gov-uk-corpus.env; set +a               # select Postgres
    python3 -m govuk_corpus.build_readability --rescan  # full re-scan (new checks)
    python3 -m govuk_corpus.build_readability            # only un-scanned rows
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Dict, Optional

from .backend import db
from .readability import CHECK_META, analyse

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"


def _batch(conn, after_url: str, size: int, rescan: bool):
    """Next `size` rows with body text (keyset paging). Normally only rows still
    needing analysis; with rescan, every row with search_text."""
    done_guard = "" if rescan else "AND gds_english_score IS NULL "
    q = (f"SELECT url, search_text FROM content "
         f"WHERE search_text IS NOT NULL {done_guard}"
         f"AND url > {_P} ORDER BY url LIMIT {int(size)}")
    return conn.execute(q, (after_url,)).fetchall()


def _summary(counts: Dict[str, int]) -> Optional[str]:
    """Readable class-level summary, e.g. 'Words to avoid: 3; Vague language: 2'."""
    if not counts:
        return None
    return "; ".join(f"{CHECK_META[k]['name']}: {n}"
                     for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def _write(conn, url: str, reading_age: Optional[float], scan: dict, findings=None) -> None:
    checks_json = json.dumps({"words": scan["words"], "counts": scan["counts"]}, separators=(",", ":"))
    conn.execute(
        f"UPDATE content SET reading_age = {_P}, gds_english_score = {_P}, "
        f"gds_findings = {_P}, gds_checks = {_P}, gds_stars = {_P} WHERE url = {_P}",
        (reading_age, scan["impact"], findings if findings is not None else _summary(scan["counts"]),
         checks_json, scan["stars"], url))


def build(conn, limit: Optional[int] = None, batch: int = 300, after: str = "",
          rescan: bool = False) -> Dict[str, int]:
    """Idempotent, resumable backfill. Per-row failures are isolated (marked done
    with a 0 impact and the error in gds_findings) so the run never loops on a bad
    row. Commits every batch. Returns counters incl. a star histogram."""
    counters = {"scanned": 0, "scored": 0, "too_short": 0, "errors": 0}
    stars_hist: Counter = Counter()
    try:
        while True:
            rows = _batch(conn, after, batch, rescan)
            if not rows:
                break
            for row in rows:
                counters["scanned"] += 1
                after = row["url"]
                findings = None
                try:
                    ra, scan = analyse(row["search_text"] or "")
                    counters["scored" if ra is not None else "too_short"] += 1
                except Exception as e:   # poison row: mark done so we don't retry it forever
                    ra, scan = None, {"words": 0, "counts": {}, "impact": 0.0, "stars": None}
                    findings = f"analysis error: {type(e).__name__}: {e}"
                    counters["errors"] += 1
                _write(conn, row["url"], ra, scan, findings)
                stars_hist[scan["stars"]] += 1
            conn.commit()
            print(f"  ...{counters['scanned']} scanned / {counters['scored']} scored / "
                  f"{counters['errors']} errors (last: {after})", flush=True)
            if limit and counters["scanned"] >= limit:
                break
    except KeyboardInterrupt:
        conn.commit()
        print("\nInterrupted — progress committed; re-run to continue.", flush=True)
    counters["last_url"] = after
    counters["stars"] = {str(k): stars_hist[k] for k in (5, 4, 3, 2, 1, None)}
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill the plain-English audit columns.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap rows this run")
    ap.add_argument("--after", default="", help="resume from a url (keyset cursor)")
    ap.add_argument("--rescan", action="store_true",
                    help="re-process every row (use after changing checks/weights)")
    args = ap.parse_args()

    if args.db and not _IS_PG:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    counters = build(conn, limit=args.limit, after=args.after, rescan=args.rescan)

    print("\nPlain-English backfill complete. Counters:")
    for k, v in counters.items():
        if k == "stars":
            print(f"  star rating   {v}   (5=healthy … 1=poor, None=too short)")
        else:
            print(f"  {k:12} {v}")
    if counters.get("last_url"):
        print(f"\nResume the next chunk with:  --after '{counters['last_url']}'")
    conn.close()


if __name__ == "__main__":
    main()
