"""Backfill content.parent_document_type from the stored content JSON.

For an html_publication the meaningful type is its parent publication's, held in the
page JSON at ``links.parent[].document_type``. This materialises that value into an
indexed column so exports/filters don't have to parse JSON at query time.

Idempotent + resumable: only rows still needing it (parent_document_type IS NULL) are
selected, so a re-run after a crash picks up where it left off. Rows with no parent are
marked done with an empty string (not re-scanned). Malformed/empty content yields no
value (also marked done). Memory-safe keyset paging by url, mirroring build_readability.

    set -a; . ~/gov-uk-corpus.env; set +a               # select Postgres
    python3 -m govuk_corpus.backfill_parent_document_type            # html_publication rows
    python3 -m govuk_corpus.backfill_parent_document_type --all      # every row
    python3 -m govuk_corpus.backfill_parent_document_type --limit 100000   # a chunk
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, Optional

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"


def extract_parent_doctype(content: Optional[str]) -> Optional[str]:
    """links.parent[].document_type from a content JSON string; None if absent/invalid.
    Tolerant of links.parent being an array (uses the first) or a single object."""
    if not content:
        return None
    try:
        obj = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    parent = (obj.get("links") or {}).get("parent")
    if isinstance(parent, list):
        parent = parent[0] if parent else None
    if isinstance(parent, dict):
        dt = parent.get("document_type")
        return str(dt) if dt else None
    return None


def _batch(conn, after_url: str, size: int, all_doctypes: bool):
    """Next `size` rows still needing the value (keyset paging by url)."""
    if all_doctypes:
        q = (f"SELECT url, content FROM content "
             f"WHERE parent_document_type IS NULL AND url > {_P} ORDER BY url LIMIT {int(size)}")
        params = (after_url,)
    else:
        q = (f"SELECT url, content FROM content "
             f"WHERE document_type = {_P} AND parent_document_type IS NULL AND url > {_P} "
             f"ORDER BY url LIMIT {int(size)}")
        params = ("html_publication", after_url)
    return conn.execute(q, params).fetchall()


def _write(conn, url: str, value: Optional[str]) -> None:
    # Empty string marks "checked, no parent" so the row is not re-scanned next run.
    conn.execute(f"UPDATE content SET parent_document_type = {_P} WHERE url = {_P}",
                 (value if value is not None else "", url))


def build(conn, limit: Optional[int] = None, batch: int = 500, after: str = "",
          all_doctypes: bool = False) -> Dict[str, int]:
    counters = {"scanned": 0, "with_parent": 0, "no_parent": 0}
    try:
        while True:
            rows = _batch(conn, after, batch, all_doctypes)
            if not rows:
                break
            for row in rows:
                counters["scanned"] += 1
                after = row["url"]
                value = extract_parent_doctype(row["content"])
                counters["with_parent" if value else "no_parent"] += 1
                _write(conn, row["url"], value)
            conn.commit()
            print(f"  ...{counters['scanned']} scanned / {counters['with_parent']} with parent "
                  f"(last: {after})", flush=True)
            if limit and counters["scanned"] >= limit:
                break
    except KeyboardInterrupt:
        conn.commit()
        print("\nInterrupted — progress committed; re-run to continue.", flush=True)
    counters["last_url"] = after
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill content.parent_document_type from content JSON.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap rows this run")
    ap.add_argument("--after", default="", help="resume from a url (keyset cursor)")
    ap.add_argument("--all", dest="all_doctypes", action="store_true",
                    help="scan every row, not just document_type='html_publication'")
    args = ap.parse_args()

    if args.db and not _IS_PG:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    counters = build(conn, limit=args.limit, after=args.after, all_doctypes=args.all_doctypes)

    print("Parent-document-type backfill complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:12} {v}")
    if counters.get("last_url"):
        print(f"\nResume the next chunk with:  --after '{counters['last_url']}'")
    conn.close()


if __name__ == "__main__":
    main()
