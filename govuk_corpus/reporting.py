"""Read-only reporting over the persisted shortlist membership.

Dashboards can read a category's filtered result (and aggregates over it) without
re-running the org/document-type/keyword filters, because the membership is already
materialised in ``category_shortlist_pages`` by the nightly cycle / on edit. Everything
here joins that table to ``content`` for page attributes.

Backend-agnostic (SQLite + Postgres). BI tools that connect straight to Postgres can use
the ``category_shortlist_report`` view (declared in the schema) instead of these functions.
"""
from __future__ import annotations

from typing import List

from . import shortlist
from .backend import db

_P = "%s" if db.__name__.endswith("db_pg") else "?"

# Effective document type without touching the raw JSON (parent_document_type is
# backfilled), so it's backend-agnostic and safe on metadata-only rows.
_EFF = ("CASE WHEN c.document_type = 'html_publication' "
        "THEN COALESCE(NULLIF(c.parent_document_type, ''), c.document_type) "
        "ELSE c.document_type END")
_PRIMARY_ORG = ("(SELECT po.organisation_slug FROM page_organisations po "
                "WHERE po.page_url = c.url AND po.role = 'primary' LIMIT 1)")
_BAND = shortlist._LAST_UPDATE_BAND_EXPR          # freshness band (reuses the shortlist def)
_READING_BAND = ("CASE WHEN c.reading_age IS NULL THEN 'Unknown' "
                 "WHEN c.reading_age < 9 THEN 'Under 9' "
                 "WHEN c.reading_age < 12 THEN '9-11' "
                 "WHEN c.reading_age < 15 THEN '12-14' "
                 "WHEN c.reading_age < 18 THEN '15-17' ELSE '18+' END")


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def overview(conn) -> List[dict]:
    """Every category with its stored input-count and its materialised shortlist size —
    the top-level dashboard table. `pages_kept` is org+doctype (no keyword);
    `membership_count` is the full org+dept+keyword shortlist."""
    return _rows(conn, """
        SELECT k.id, k.slug, k.description, k.owner_email, k.status, k.updated_at,
               pc.pages_kept, pc.computed_at,
               (SELECT COUNT(*) FROM category_shortlist_pages m WHERE m.category_id = k.id)
                   AS membership_count
        FROM categories k
        LEFT JOIN category_page_counts pc ON pc.category_id = k.id
        ORDER BY k.created_at DESC
    """)


def category_pages(conn, cid: int, limit: int = 100, offset: int = 0) -> dict:
    """One page of a category's shortlist membership, joined to content attributes."""
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM category_shortlist_pages WHERE category_id = {_P}",
        (cid,)).fetchone()["n"]
    rows = _rows(conn, f"""
        SELECT m.content_id, m.url, c.title,
               {_EFF} AS effective_document_type, c.document_type,
               c.public_updated_at, c.first_published_at,
               c.reading_age, c.gds_stars, c.gds_english_score,
               {_PRIMARY_ORG} AS primary_org
        FROM category_shortlist_pages m
        JOIN content c ON c.url = m.url
        WHERE m.category_id = {_P}
        ORDER BY m.content_id
        LIMIT {_P} OFFSET {_P}
    """, (cid, limit, offset))
    return {"total": total, "rows": rows, "limit": limit, "offset": offset}


def _group(conn, cid: int, expr: str) -> List[dict]:
    return _rows(conn, f"""
        SELECT {expr} AS key, COUNT(*) AS count
        FROM category_shortlist_pages m JOIN content c ON c.url = m.url
        WHERE m.category_id = {_P}
        GROUP BY {expr} ORDER BY count DESC, key
    """, (cid,))


def category_summary(conn, cid: int) -> dict:
    """Aggregates over a category's materialised shortlist — for dashboard charts.
    No filter re-execution: it reads category_shortlist_pages joined to content."""
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM category_shortlist_pages WHERE category_id = {_P}",
        (cid,)).fetchone()["n"]
    computed_at = conn.execute(
        f"SELECT MAX(computed_at) AS t FROM category_shortlist_pages WHERE category_id = {_P}",
        (cid,)).fetchone()["t"]
    avg_reading = conn.execute(f"""
        SELECT AVG(c.reading_age) AS a
        FROM category_shortlist_pages m JOIN content c ON c.url = m.url
        WHERE m.category_id = {_P} AND c.reading_age IS NOT NULL
    """, (cid,)).fetchone()["a"]
    return {
        "category_id": cid,
        "total": total,
        "computed_at": computed_at,
        "avg_reading_age": round(avg_reading, 1) if avg_reading is not None else None,
        "by_document_type": _group(conn, cid, _EFF),
        "by_organisation": _group(conn, cid, _PRIMARY_ORG),
        "by_last_update_band": _group(conn, cid, _BAND),
        "by_reading_age_band": _group(conn, cid, _READING_BAND),
        "by_gds_stars": _group(conn, cid, "c.gds_stars"),
    }
