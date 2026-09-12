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
