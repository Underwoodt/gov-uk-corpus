"""One-time migration: load the existing SQLite content.db into the new schema.

Reads the OLD-format SQLite database (tables `content` + `sitemap` as produced by
the earlier jobs/ scripts) and writes into the current schema via the active
backend (SQLite locally, Postgres on the server when DB_HOST is set). URLs are
canonicalised on the way in; content_hash is computed from the stored JSON so the
next incremental run can detect real changes.

Quote-free to run (no SQL typed at the shell):
    # server (Postgres via env)
    set -a; . ~/gov-uk-corpus.env; set +a
    python3 -m govuk_corpus.migrate_sqlite --sqlite ~/content.db
    # local test (SQLite target), small sample
    python3 -m govuk_corpus.migrate_sqlite --sqlite ~/Downloads/content.db --db data/mig.db --limit 500
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from typing import Dict, List, Optional

from .backend import db
from .canonical import canonicalise
from .hashing import content_hash

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"
_CONFLICT = "ON CONFLICT DO NOTHING" if _IS_PG else "OR IGNORE"

_CONTENT_COLS = [
    "url", "content", "content_hash", "source", "document_type", "title",
    "description", "destination_url", "http_status", "is_redirect",
    "sitemap_lastmod", "first_seen_run", "last_seen_run", "last_changed_run",
    "first_seen_at", "last_seen_at", "last_changed_at",
]


def _content_insert_sql() -> str:
    cols = ",".join(_CONTENT_COLS)
    ph = ",".join([_P] * len(_CONTENT_COLS))
    if _IS_PG:
        return f"INSERT INTO content ({cols}) VALUES ({ph}) ON CONFLICT DO NOTHING"
    return f"INSERT OR IGNORE INTO content ({cols}) VALUES ({ph})"


def _open_source(path: str) -> sqlite3.Connection:
    uri = f"file:{os.path.expanduser(path)}?mode=ro&immutable=1"
    src = sqlite3.connect(uri, uri=True)
    src.row_factory = sqlite3.Row
    return src


def migrate(conn, src: sqlite3.Connection, run_id: str, ts: str,
            limit: Optional[int] = None, batch: int = 2000) -> Dict[str, int]:
    counters = {k: 0 for k in ("sitemap_in", "sitemap_written", "content_in",
                               "content_written", "invalid_url")}

    # --- sitemap first (so we can tag content source + carry lastmod) ---
    lastmod_by_url: Dict[str, Optional[str]] = {}
    for row in src.execute("SELECT url, sitemap_file, lastmod FROM sitemap"):
        counters["sitemap_in"] += 1
        url = canonicalise(row["url"])
        if url is None:
            counters["invalid_url"] += 1
            continue
        db.upsert_sitemap(conn, url, row["sitemap_file"], row["lastmod"])
        lastmod_by_url[url] = row["lastmod"]
        counters["sitemap_written"] += 1
        if counters["sitemap_written"] % 20000 == 0:
            conn.commit()
    conn.commit()

    # --- content ---
    insert_sql = _content_insert_sql()
    q = ("SELECT url, content, document_type, title, description, destination_url, status_code "
         "FROM content")
    if limit:
        q += f" LIMIT {int(limit)}"
    params_batch: List[tuple] = []
    for row in src.execute(q):
        counters["content_in"] += 1
        url = canonicalise(row["url"])
        if url is None:
            counters["invalid_url"] += 1
            continue
        raw = row["content"]
        chash = content_hash(raw) if raw else None
        is_redirect = 1 if (row["document_type"] == "redirect") else 0
        source = "sitemap" if url in lastmod_by_url else "other"
        params_batch.append((
            url, raw, chash, source, row["document_type"], row["title"],
            row["description"], row["destination_url"], row["status_code"], is_redirect,
            lastmod_by_url.get(url), run_id, run_id, run_id, ts, ts, ts,
        ))
        if len(params_batch) >= batch:
            conn.cursor().executemany(insert_sql, params_batch)
            conn.commit()
            counters["content_written"] += len(params_batch)
            params_batch.clear()
            print(f"  ...{counters['content_written']} content rows", flush=True)
    if params_batch:
        conn.cursor().executemany(insert_sql, params_batch)
        conn.commit()
        counters["content_written"] += len(params_batch)
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate the old SQLite content.db into the new schema.")
    ap.add_argument("--sqlite", required=True, help="path to the existing content.db (read-only)")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite target (ignored for Postgres)")
    ap.add_argument("--limit", type=int, help="cap content rows (for testing)")
    ap.add_argument("--scope", default="migration")
    args = ap.parse_args()

    if args.db:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    src = _open_source(args.sqlite)

    run_id = db.start_run(conn, stage="migrate", scope=args.scope)
    ts = db.now_iso()
    print(f"Migrating from {args.sqlite} -> backend {db.__name__.split('.')[-1]} (run {run_id[:8]})")
    counters = migrate(conn, src, run_id, ts, limit=args.limit)
    db.finish_run(conn, run_id, counters)
    src.close()

    print("\nMigration complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:18} {v}")
    total = conn.execute("SELECT COUNT(*) AS n FROM content").fetchone()["n"]
    sm = conn.execute("SELECT COUNT(*) AS n FROM sitemap").fetchone()["n"]
    print(f"\nTarget now: {total} content rows, {sm} sitemap rows.")
    conn.close()


if __name__ == "__main__":
    main()
