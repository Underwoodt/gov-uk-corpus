"""Pure metric queries for the dashboard (backend-agnostic, unit-tested).

No Streamlit here — these functions return plain data so they can be tested against
SQLite and reused by any UI. All read-only aggregates; standard SQL that runs on both
SQLite and Postgres.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Counter keys that represent a failed/again-needed outcome, per stage.
_BAD_KEYS = frozenset({
    "error", "invalid", "no_content_item", "failed",
    "circular", "max_depth", "gone", "external",
    "imported_error", "imported_no_content_item",
})
# Keys that are totals/labels, not per-item outcomes — excluded from rate maths.
_META_KEYS = frozenset({
    "seen", "sources", "parents", "urls_seen", "sub_sitemaps",
    "children_to_import", "child_links", "binaries", "duplicate",
    "resolved", "content_in", "sitemap_in",
})


def corpus_totals(conn) -> Dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN content_hash IS NOT NULL THEN 1 ELSE 0 END) AS fetched, "
        "SUM(CASE WHEN is_redirect=1 THEN 1 ELSE 0 END) AS redirects "
        "FROM content"
    ).fetchone()
    total = row["total"] or 0
    fetched = row["fetched"] or 0
    sitemap_total = conn.execute("SELECT COUNT(*) AS n FROM sitemap").fetchone()["n"] or 0
    backlog = conn.execute(
        "SELECT COUNT(*) AS n FROM sitemap s LEFT JOIN content c ON c.url = s.url "
        "WHERE c.url IS NULL OR c.content_hash IS NULL"
    ).fetchone()["n"] or 0
    links = conn.execute("SELECT COUNT(*) AS n FROM page_links").fetchone()["n"] or 0
    return {
        "total": total,
        "fetched": fetched,
        "unfetched": total - fetched,
        "redirects": row["redirects"] or 0,
        "sitemap_total": sitemap_total,
        "backlog_remaining": backlog,
        "page_links": links,
        "pct_fetched": round(100.0 * fetched / total, 1) if total else 0.0,
    }


def source_breakdown(conn) -> List[Tuple[str, int]]:
    rows = conn.execute(
        "SELECT COALESCE(source, '(none)') AS source, COUNT(*) AS n "
        "FROM content GROUP BY source ORDER BY n DESC"
    ).fetchall()
    return [(r["source"], r["n"]) for r in rows]


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _elapsed_seconds(started: Optional[str], finished: Optional[str]) -> Optional[float]:
    a, b = _parse_dt(started), _parse_dt(finished)
    if a and b:
        return round((b - a).total_seconds(), 1)
    return None


def _run_health(counters: Dict[str, Any]) -> Dict[str, Any]:
    """Errors and success-rate derived from a run's counters."""
    errors = sum(int(v) for k, v in counters.items()
                 if k in _BAD_KEYS and isinstance(v, (int, float)))
    good = sum(int(v) for k, v in counters.items()
               if isinstance(v, (int, float)) and k not in _BAD_KEYS and k not in _META_KEYS)
    attempted = good + errors
    rate = round(100.0 * good / attempted, 1) if attempted else 100.0
    return {"errors": errors, "processed": good, "success_rate": rate}


def recent_runs(conn, n: int = 7) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT run_id, stage, status, started_at, finished_at, counters "
        f"FROM runs ORDER BY started_at DESC LIMIT {int(n)}"
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        counters = {}
        if r["counters"]:
            try:
                counters = json.loads(r["counters"])
            except (ValueError, TypeError):
                counters = {}
        health = _run_health(counters)
        out.append({
            "run_id": r["run_id"],
            "short_id": (r["run_id"] or "")[:8],
            "stage": r["stage"],
            "status": r["status"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "elapsed_s": _elapsed_seconds(r["started_at"], r["finished_at"]),
            "errors": health["errors"],
            "processed": health["processed"],
            "success_rate": health["success_rate"],
        })
    return out


def active_runs(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE status='running'"
    ).fetchone()["n"] or 0
