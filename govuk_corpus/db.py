"""SQLite access for the pilot (Postgres-ready: parameterised SQL, schema in .sql)."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.commit()


# ---- runs -----------------------------------------------------------------

def start_run(conn: sqlite3.Connection, stage: str, scope: str) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO runs (run_id, started_at, status, stage, scope) VALUES (?,?,?,?,?)",
        (run_id, now_iso(), "running", stage, scope),
    )
    conn.commit()
    return run_id


def finish_run(conn: sqlite3.Connection, run_id: str, counters: Dict[str, int],
               status: str = "complete") -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, counters=? WHERE run_id=?",
        (now_iso(), status, json.dumps(counters), run_id),
    )
    conn.commit()


def log_fetch(conn: sqlite3.Connection, run_id: str, stage: str, url: str, action: str,
              http_status: Optional[int] = None, bytes_: Optional[int] = None,
              duration_ms: Optional[int] = None, error: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO fetch_log (run_id, stage, url, action, http_status, bytes, duration_ms, error, ts)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, stage, url, action, http_status, bytes_, duration_ms, error, now_iso()),
    )


# ---- content --------------------------------------------------------------

def get_content_hash(conn: sqlite3.Connection, url: str) -> Optional[str]:
    row = conn.execute("SELECT content_hash FROM content WHERE url=?", (url,)).fetchone()
    return row["content_hash"] if row else None


def upsert_content(conn: sqlite3.Connection, url: str, run_id: str, fields: Dict[str, Any],
                   changed: bool, first_time: bool) -> None:
    """Insert or update a content row and maintain provenance columns."""
    ts = now_iso()
    if first_time:
        cols = dict(fields)
        cols.update({
            "url": url,
            "first_seen_run": run_id, "last_seen_run": run_id, "last_changed_run": run_id,
            "first_seen_at": ts, "last_seen_at": ts, "last_changed_at": ts,
        })
        placeholders = ",".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO content ({','.join(cols)}) VALUES ({placeholders})",
            tuple(cols.values()),
        )
    else:
        sets = {**fields, "last_seen_run": run_id, "last_seen_at": ts}
        if changed:
            sets["last_changed_run"] = run_id
            sets["last_changed_at"] = ts
        assignments = ",".join(f"{k}=?" for k in sets)
        conn.execute(
            f"UPDATE content SET {assignments} WHERE url=?",
            tuple(sets.values()) + (url,),
        )


def replace_page_organisations(conn: sqlite3.Connection, url: str, orgs: list) -> None:
    conn.execute("DELETE FROM page_organisations WHERE page_url=?", (url,))
    for o in orgs:
        conn.execute(
            "INSERT OR IGNORE INTO page_organisations "
            "(page_url, organisation_content_id, organisation_slug, role) VALUES (?,?,?,?)",
            (url, o.get("content_id"), o.get("slug"), o.get("role")),
        )


# ---- sitemap (Stage 0) ----------------------------------------------------

def upsert_sitemap(conn: sqlite3.Connection, url: str, sitemap_file: str,
                   lastmod: Optional[str]) -> str:
    """Insert or update one frontier URL. Returns 'new' | 'updated' | 'unchanged'."""
    row = conn.execute("SELECT lastmod FROM sitemap WHERE url=?", (url,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO sitemap (url, sitemap_file, lastmod, imported_at) VALUES (?,?,?,?)",
            (url, sitemap_file, lastmod, now_iso()),
        )
        return "new"
    if (row["lastmod"] or "") != (lastmod or ""):
        conn.execute(
            "UPDATE sitemap SET sitemap_file=?, lastmod=?, imported_at=? WHERE url=?",
            (sitemap_file, lastmod, now_iso(), url),
        )
        return "updated"
    return "unchanged"


def sitemap_frontier(conn: sqlite3.Connection, limit: Optional[int] = None):
    """URLs needing (re)fetch: not in content, or the sitemap lastmod is newer.

    NOTE: lastmod comparison is lexicographic on the ISO-8601 strings, which is
    correct while GOV.UK emits a consistent timezone/format (it does).
    """
    q = (
        "SELECT s.url AS url, s.lastmod AS lastmod "
        "FROM sitemap s LEFT JOIN content c ON c.url = s.url "
        "WHERE c.url IS NULL "                       # not in corpus at all
        "   OR c.content_hash IS NULL "              # row exists but never successfully fetched (e.g. migrated backlog)
        "   OR (s.lastmod IS NOT NULL AND s.lastmod <> '' "
        "        AND (c.sitemap_lastmod IS NULL OR s.lastmod > c.sitemap_lastmod)) "  # sitemap says newer
        "ORDER BY s.url"
    )
    params: tuple = ()
    if limit:
        q += " LIMIT ?"
        params = (limit,)
    return conn.execute(q, params).fetchall()


# ---- page links / existence (Stage 3) -------------------------------------

def content_exists(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM content WHERE url=?", (url,)).fetchone() is not None


def add_page_link(conn: sqlite3.Connection, parent_url: str, child_url: str,
                  relation: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO page_links (parent_url, child_url, relation) VALUES (?,?,?)",
        (parent_url, child_url, relation),
    )


# ---- redirects (Stage 2) --------------------------------------------------

def find_unresolved_redirects(conn: sqlite3.Connection, limit: Optional[int] = None):
    """Redirect pages not yet resolved into the `redirects` table."""
    q = (
        "SELECT url FROM content "
        "WHERE is_redirect = 1 AND url NOT IN (SELECT source_url FROM redirects) "
        "ORDER BY url"
    )
    params: tuple = ()
    if limit:
        q += " LIMIT ?"
        params = (limit,)
    return [r["url"] for r in conn.execute(q, params).fetchall()]


def upsert_redirect(conn: sqlite3.Connection, source_url: str,
                    destination_url: Optional[str], http_status: Optional[int],
                    run_id: str) -> None:
    conn.execute(
        "INSERT INTO redirects (source_url, destination_url, http_status, resolved_run, resolved_at) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(source_url) DO UPDATE SET "
        "destination_url=excluded.destination_url, http_status=excluded.http_status, "
        "resolved_run=excluded.resolved_run, resolved_at=excluded.resolved_at",
        (source_url, destination_url, http_status, run_id, now_iso()),
    )
    # Keep the source page's destination pointing at the FINAL target.
    conn.execute("UPDATE content SET destination_url=? WHERE url=?", (destination_url, source_url))
