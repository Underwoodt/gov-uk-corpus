"""Postgres backend — mirrors db.py's API so the stages run unchanged.

Selected by govuk_corpus.backend when DB_HOST (or DB_BACKEND=postgres) is set.
Connection details come from env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
Kept intentionally parallel to db.py: same function names/signatures, `%s`
placeholders, `ON CONFLICT DO NOTHING` in place of SQLite's `INSERT OR IGNORE`.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import psycopg
from psycopg.rows import dict_row

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema_pg.sql")
GOLD_VOTES_BACKFILL = """INSERT INTO category_gold_votes (category_id, url, labeller, label, rationale, labelled_at, content_id, content_hash_at_label, stratum_score_band, stratum_source, stratum_doc_type, seed_origin, sample_run_id, sample_stage, sample_frac) SELECT category_id, url, COALESCE(labelled_by, 'unknown'), label, rationale, labelled_at, content_id, content_hash_at_label, stratum_score_band, stratum_source, stratum_doc_type, seed_origin, sample_run_id, sample_stage, sample_frac FROM category_gold_labels g WHERE g.label IS NOT NULL AND g.adjudicated_at IS NULL AND NOT EXISTS (SELECT 1 FROM category_gold_votes v WHERE v.category_id = g.category_id AND v.url = g.url)"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST', 'localhost')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'gov_uk_corpus')} "
        f"user={os.getenv('DB_USER', 'corpus')} "
        f"password={os.getenv('DB_PASSWORD', '')}"
    )


def connect(_path: Optional[str] = None, statement_timeout_ms: Optional[int] = None):
    """Connect using env DSN. `_path` is accepted for signature parity with db.py.

    `statement_timeout_ms` sets a per-connection statement timeout (used by the
    dashboard so an expensive ad-hoc query can never hang the UI).
    """
    kwargs = {"row_factory": dict_row}
    if statement_timeout_ms:
        kwargs["options"] = f"-c statement_timeout={int(statement_timeout_ms)}"
    return psycopg.connect(_dsn(), **kwargs)


def init_db(conn) -> None:
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        conn.execute(fh.read())
    # Lightweight, idempotent column migrations for tables that already exist on prod
    # (CREATE TABLE IF NOT EXISTS above is a no-op for them, so new columns need ALTER).
    conn.execute("ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS raw_reply text")
    # The inclusion pass's grounding fields (see evaluate.parse_decision): primary_topic is fed to
    # Phase 2 as {{PASS1_TOPIC}}; where_hit / evidence are JSON lists for the judge.
    conn.execute("ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS primary_topic text")
    conn.execute("ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS where_hit text")
    conn.execute("ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS evidence text")
    # The content.content_hash of the body actually evaluated — drift detection vs gold labels.
    conn.execute("ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS content_hash text")
    conn.execute("ALTER TABLE evaluation_results ADD COLUMN IF NOT EXISTS stop_reason text")
    # Live run state: is a driver actively working this run, on which process, and when did it
    # last make progress — so a stalled run (marked running but its process is gone) is detectable.
    conn.execute("ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS run_status text")
    conn.execute("ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS pid integer")
    conn.execute("ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS host text")
    conn.execute("ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS heartbeat_at text")
    # A JSON snapshot of the prompt inputs a run used, so its prompts are exactly reproducible.
    conn.execute("ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS prompt_spec text")
    # Sampling provenance on gold labels (guc-0029 stage-stratified picks).
    conn.execute("ALTER TABLE category_gold_labels ADD COLUMN IF NOT EXISTS sample_run_id text")
    conn.execute("ALTER TABLE category_gold_labels ADD COLUMN IF NOT EXISTS sample_stage text")
    conn.execute("ALTER TABLE category_gold_labels ADD COLUMN IF NOT EXISTS sample_frac double precision")
    for col, typ in (("n_votes", "integer"), ("agreement", "text"), ("adjudicated_by", "text"),
                     ("adjudicated_at", "text"), ("resolution_note", "text")):
        conn.execute(f"ALTER TABLE category_gold_labels ADD COLUMN IF NOT EXISTS {col} {typ}")
    # Multi-labeller store: a consensus row that predates votes becomes its labeller's vote.
    conn.execute(GOLD_VOTES_BACKFILL)
    # GOV.UK Search view_count (~14-day pageviews) + the date it was collected.
    conn.execute("ALTER TABLE content ADD COLUMN IF NOT EXISTS view_count integer")
    conn.execute("ALTER TABLE content ADD COLUMN IF NOT EXISTS view_count_updated text")
    conn.execute("ALTER TABLE content ADD COLUMN IF NOT EXISTS view_count_source text")
    conn.commit()


# ---- runs -----------------------------------------------------------------

def start_run(conn, stage: str, scope: str) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO runs (run_id, started_at, status, stage, scope) VALUES (%s,%s,%s,%s,%s)",
        (run_id, now_iso(), "running", stage, scope),
    )
    conn.commit()
    return run_id


def finish_run(conn, run_id: str, counters: Dict[str, int], status: str = "complete") -> None:
    conn.execute(
        "UPDATE runs SET finished_at=%s, status=%s, counters=%s WHERE run_id=%s",
        (now_iso(), status, json.dumps(counters), run_id),
    )
    conn.commit()


def log_fetch(conn, run_id: str, stage: str, url: str, action: str,
              http_status: Optional[int] = None, bytes_: Optional[int] = None,
              duration_ms: Optional[int] = None, error: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO fetch_log (run_id, stage, url, action, http_status, bytes, duration_ms, error, ts)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (run_id, stage, url, action, http_status, bytes_, duration_ms, error, now_iso()),
    )


# ---- content --------------------------------------------------------------

def get_content_hash(conn, url: str) -> Optional[str]:
    row = conn.execute("SELECT content_hash FROM content WHERE url=%s", (url,)).fetchone()
    return row["content_hash"] if row else None


def upsert_content(conn, url: str, run_id: str, fields: Dict[str, Any],
                   changed: bool, first_time: bool) -> None:
    ts = now_iso()
    if first_time:
        cols = dict(fields)
        cols.update({
            "url": url,
            "first_seen_run": run_id, "last_seen_run": run_id, "last_changed_run": run_id,
            "first_seen_at": ts, "last_seen_at": ts, "last_changed_at": ts,
        })
        placeholders = ",".join(["%s"] * len(cols))
        conn.execute(
            f"INSERT INTO content ({','.join(cols)}) VALUES ({placeholders})",
            tuple(cols.values()),
        )
    else:
        sets = {**fields, "last_seen_run": run_id, "last_seen_at": ts}
        if changed:
            sets["last_changed_run"] = run_id
            sets["last_changed_at"] = ts
        assignments = ",".join(f"{k}=%s" for k in sets)
        conn.execute(
            f"UPDATE content SET {assignments} WHERE url=%s",
            tuple(sets.values()) + (url,),
        )


def replace_page_organisations(conn, url: str, orgs: list) -> None:
    conn.execute("DELETE FROM page_organisations WHERE page_url=%s", (url,))
    for o in orgs:
        conn.execute(
            "INSERT INTO page_organisations "
            "(page_url, organisation_content_id, organisation_slug, role) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (url, o.get("content_id"), o.get("slug"), o.get("role")),
        )


# ---- sitemap (Stage 0) ----------------------------------------------------

def upsert_sitemap(conn, url: str, sitemap_file: str, lastmod: Optional[str]) -> str:
    row = conn.execute("SELECT lastmod FROM sitemap WHERE url=%s", (url,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO sitemap (url, sitemap_file, lastmod, imported_at) VALUES (%s,%s,%s,%s)",
            (url, sitemap_file, lastmod, now_iso()),
        )
        return "new"
    if (row["lastmod"] or "") != (lastmod or ""):
        conn.execute(
            "UPDATE sitemap SET sitemap_file=%s, lastmod=%s, imported_at=%s WHERE url=%s",
            (sitemap_file, lastmod, now_iso(), url),
        )
        return "updated"
    return "unchanged"


def sitemap_frontier(conn, limit: Optional[int] = None):
    q = (
        "SELECT s.url AS url, s.lastmod AS lastmod "
        "FROM sitemap s LEFT JOIN content c ON c.url = s.url "
        "WHERE c.url IS NULL "
        "   OR c.content_hash IS NULL "
        "   OR (s.lastmod IS NOT NULL AND s.lastmod <> '' "
        "        AND (c.sitemap_lastmod IS NULL OR s.lastmod > c.sitemap_lastmod)) "
        "ORDER BY s.url"
    )
    params: tuple = ()
    if limit:
        q += " LIMIT %s"
        params = (limit,)
    return conn.execute(q, params).fetchall()


# ---- page links / existence (Stage 3) -------------------------------------

def content_exists(conn, url: str) -> bool:
    return conn.execute("SELECT 1 FROM content WHERE url=%s", (url,)).fetchone() is not None


def set_search_text(conn, url: str, text: str) -> None:
    conn.execute("UPDATE content SET search_text=%s WHERE url=%s", (text, url))


def add_page_link(conn, parent_url: str, child_url: str, relation: str) -> None:
    conn.execute(
        "INSERT INTO page_links (parent_url, child_url, relation) VALUES (%s,%s,%s) "
        "ON CONFLICT DO NOTHING",
        (parent_url, child_url, relation),
    )


# ---- redirects (Stage 2) --------------------------------------------------

def find_unresolved_redirects(conn, limit: Optional[int] = None):
    q = (
        "SELECT url FROM content "
        "WHERE is_redirect = 1 AND url NOT IN (SELECT source_url FROM redirects) "
        "ORDER BY url"
    )
    params: tuple = ()
    if limit:
        q += " LIMIT %s"
        params = (limit,)
    return [r["url"] for r in conn.execute(q, params).fetchall()]


def upsert_redirect(conn, source_url: str, destination_url: Optional[str],
                    http_status: Optional[int], run_id: str) -> None:
    conn.execute(
        "INSERT INTO redirects (source_url, destination_url, http_status, resolved_run, resolved_at) "
        "VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT(source_url) DO UPDATE SET "
        "destination_url=excluded.destination_url, http_status=excluded.http_status, "
        "resolved_run=excluded.resolved_run, resolved_at=excluded.resolved_at",
        (source_url, destination_url, http_status, run_id, now_iso()),
    )
    conn.execute("UPDATE content SET destination_url=%s WHERE url=%s", (destination_url, source_url))
