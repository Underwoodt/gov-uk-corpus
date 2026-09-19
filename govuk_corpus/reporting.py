"""Read-only reporting over the persisted shortlist membership.

Dashboards can read a category's filtered result (and aggregates over it) without
re-running the org/document-type/keyword filters, because the membership is already
materialised in ``category_shortlist_pages`` by the nightly cycle / on edit. Everything
here joins that table to ``content`` for page attributes.

Backend-agnostic (SQLite + Postgres). BI tools that connect straight to Postgres can use
the ``category_shortlist_report`` view (declared in the schema) instead of these functions.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import List, Tuple

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


def membership_count(conn, cid: int) -> int:
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM category_shortlist_pages WHERE category_id = {_P}",
        (cid,)).fetchone()["n"]


def membership_computed_at(conn, cid: int):
    return conn.execute(
        f"SELECT MAX(computed_at) AS t FROM category_shortlist_pages WHERE category_id = {_P}",
        (cid,)).fetchone()["t"]


# ---- breakdowns over the materialised shortlist (instant; no corpus scan) ----

def org_breakdown_query(cid: int, organisations, limit: int = 300):
    """(sql, params) — pages in the materialised shortlist contributed by each of the
    category's organisations. Counts distinct content_id; organisations overlap."""
    if not organisations:
        return None, []
    ph = ",".join([_P] * len(organisations))
    sql = (f"SELECT po.organisation_slug AS org, COUNT(DISTINCT COALESCE(c.content_id, c.url)) AS n "
           f"FROM category_shortlist_pages m "
           f"JOIN content c ON c.url = m.url "
           f"JOIN page_organisations po ON po.page_url = c.url "
           f"WHERE m.category_id = {_P} AND po.organisation_slug IN ({ph}) "
           f"GROUP BY po.organisation_slug ORDER BY n DESC, po.organisation_slug LIMIT {int(limit)}")
    return sql, [cid] + list(organisations)


def org_breakdown(conn, cid: int, organisations) -> List[dict]:
    sql, params = org_breakdown_query(cid, organisations)
    if sql is None:
        return []
    return [{"organisation": r["org"], "count": r["n"]}
            for r in conn.execute(sql, tuple(params)).fetchall()]


def doctype_breakdown_query(cid: int, limit: int = 300):
    """(sql, params) — pages in the materialised shortlist by effective document type
    (html_publication rolled up to its parent), counting distinct content_id."""
    sql = (f"SELECT {_EFF} AS dt, COUNT(DISTINCT COALESCE(c.content_id, c.url)) AS n "
           f"FROM category_shortlist_pages m JOIN content c ON c.url = m.url "
           f"WHERE m.category_id = {_P} "
           f"GROUP BY {_EFF} ORDER BY n DESC, dt LIMIT {int(limit)}")
    return sql, [cid]


def doctype_breakdown(conn, cid: int) -> List[dict]:
    sql, params = doctype_breakdown_query(cid)
    return [{"document_type": r["dt"], "count": r["n"]}
            for r in conn.execute(sql, tuple(params)).fetchall() if r["dt"]]


def _matched_sets(conn, cid: int) -> Tuple[List[set], bool]:
    """(per-page matched-keyword sets, has_stored_data) from category_shortlist_pages.
    has_stored_data is False only when every row's matched_keywords is NULL (membership
    built before this column existed) — the caller then falls back to live matching."""
    sets: List[set] = []
    has = False
    for r in conn.execute(
            f"SELECT matched_keywords FROM category_shortlist_pages WHERE category_id = {_P}",
            (cid,)).fetchall():
        v = dict(r).get("matched_keywords")
        if v is not None:
            has = True
        try:
            lst = json.loads(v) if v else []
        except Exception:
            lst = []
        sets.append(set(lst) if isinstance(lst, list) else set())
    return sets, has


def has_matched_keywords(conn, cid: int) -> bool:
    """True when this category's membership carries stored keyword hits (the single source
    of truth the charts read); False for pre-upgrade rows that need live re-matching."""
    row = conn.execute(
        f"SELECT 1 FROM category_shortlist_pages "
        f"WHERE category_id = {_P} AND matched_keywords IS NOT NULL LIMIT 1", (cid,)).fetchone()
    return row is not None


def matched_keywords_sql(cid: int) -> str:
    """Display SQL for the charts that derive from the stored hits (a single read; the
    per-keyword counting happens in Python from matched_keywords)."""
    return ("-- Keyword hits are stored per page in category_shortlist_pages.matched_keywords\n"
            "-- (a JSON array set when the shortlist is built), so the row lozenges and these\n"
            "-- charts share one source of truth. Counting is done in Python from:\n"
            f"SELECT matched_keywords FROM category_shortlist_pages WHERE category_id = {cid};")


def keyword_count_query(cid: int, keyword: str, match: str = "any"):
    """(sql, params) — how many pages in the materialised shortlist contain one keyword.
    Scoped to the shortlist (a few thousand rows), so it's fast and can't time out even
    for very common terms — unlike the old whole-corpus per-term count."""
    clause, kwp = shortlist._keyword_clause([keyword], match, shortlist._IS_PG)
    sql = (f"SELECT COUNT(*) AS n FROM category_shortlist_pages m "
           f"JOIN content c ON c.url = m.url "
           f"WHERE m.category_id = {_P} AND {clause}")
    return sql, [cid] + list(kwp)


def keyword_overlap(conn, cid: int, keywords, match: str = "any") -> dict:
    """Overlap of the given keywords within the materialised shortlist, as region counts for
    an UpSet/Venn. Region mask == the set of keywords a page matched (bit i = keywords[i]);
    the empty region (matches none) is dropped. Derives from the per-page keyword hits stored
    in category_shortlist_pages.matched_keywords — the same values shown as row lozenges — so
    the chart and the rows agree by construction. Falls back to live matching for a category
    whose membership predates that column."""
    keywords = list(keywords)
    if not keywords:
        return {"keywords": [], "regions": [], "sql": "-- No keywords."}
    sets, has = _matched_sets(conn, cid)
    if not has:
        # Fallback: recompute from content with the per-keyword clause (pre-upgrade rows).
        parts, params = [], []
        for i, kw in enumerate(keywords):
            clause, kwp = shortlist._keyword_clause([kw], match, shortlist._IS_PG)
            parts.append(f"CASE WHEN {clause} THEN {1 << i} ELSE 0 END")
            params.extend(kwp)
        params.append(cid)
        sql = (f"SELECT ({' + '.join(parts)}) AS mask, COUNT(*) AS n "
               f"FROM category_shortlist_pages m JOIN content c ON c.url = m.url "
               f"WHERE m.category_id = {_P} GROUP BY 1")
        regions = [{"mask": int(r["mask"]), "count": r["n"]}
                   for r in conn.execute(sql, tuple(params)).fetchall() if int(r["mask"]) != 0]
        return {"keywords": keywords, "regions": regions, "sql": (sql, params)}
    idx = {kw: i for i, kw in enumerate(keywords)}
    counts: Counter = Counter()
    for s in sets:
        mask = 0
        for kw in s:
            if kw in idx:
                mask |= (1 << idx[kw])
        if mask:
            counts[mask] += 1
    regions = [{"mask": m, "count": n} for m, n in counts.items()]
    return {"keywords": keywords, "regions": regions, "sql": matched_keywords_sql(cid)}


def keyword_breakdown(conn, cid: int, keywords, match: str = "any") -> List[dict]:
    """Pages matching EACH keyword within the shortlist. Reads the stored per-page hits
    (single source of truth); falls back to a live per-keyword count for pre-upgrade rows."""
    keywords = list(keywords)
    sets, has = _matched_sets(conn, cid)
    out = []
    if has:
        for kw in keywords:
            out.append({"keyword": kw, "count": sum(1 for s in sets if kw in s)})
    else:
        for kw in keywords:
            sql, params = keyword_count_query(cid, kw, match)
            try:
                n = conn.execute(sql, tuple(params)).fetchone()["n"]
            except Exception:
                n = None
            out.append({"keyword": kw, "count": n})
    out.sort(key=lambda t: (t["count"] is None, -(t["count"] or 0)))
    return out


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
