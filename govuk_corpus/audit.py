"""Per-page funnel audit for a category.

For a category, log every page that passes the ORGANISATION filter (the starting
point — so we never scan/export the whole corpus) and record where it dropped:

    included               passed organisation + document type + keyword
    dropped: document type passed organisation, wrong document type
    dropped: keyword       passed organisation + document type, no keyword match

Stored in `category_audit` (indexed by category_id + outcome) so it is easy to
query and export by outcome. Backend-agnostic.

    python3 -m govuk_corpus.audit --db data/pilot.db --category <id>
"""
from __future__ import annotations

import argparse
from typing import Dict, List, Optional, Sequence, Tuple

from .backend import db
from .shortlist import _keyword_clause, doctype_clause

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

OUTCOME_DOCTYPE = "dropped: document type"
OUTCOME_KEYWORD = "dropped: keyword"
OUTCOME_INCLUDED = "included"


def _build_select(organisations: Sequence[str], document_types: Sequence[str],
                  keywords: Sequence[str], keyword_scope: str = "anywhere") -> Tuple[str, list]:
    """Rows in the org set, each tagged pass_dt / pass_kw. Params ordered
    document-type, keyword, organisation to match the SQL text."""
    params: list = []

    if document_types:
        dt_ok = doctype_clause(document_types)   # html_publication matches on parent's type
        params.extend(document_types)
    else:
        dt_ok = "1=1"

    if keywords:
        clause, kw_params = _keyword_clause(keywords, "any", _IS_PG, keyword_scope)
        kw_ok = clause
        params.extend(kw_params)
    else:
        kw_ok = "1=1"

    org_ph = ",".join([_P] * len(organisations))
    params.extend(organisations)

    sql = (
        f"SELECT c.url AS url, "
        f"CASE WHEN {dt_ok} THEN 1 ELSE 0 END AS pass_dt, "
        f"CASE WHEN {kw_ok} THEN 1 ELSE 0 END AS pass_kw "
        f"FROM content c "
        f"WHERE c.is_redirect = 0 AND c.content_hash IS NOT NULL "
        f"AND EXISTS (SELECT 1 FROM page_organisations po "
        f"WHERE po.page_url = c.url AND po.organisation_slug IN ({org_ph})) "
        f"ORDER BY c.url"
    )
    return sql, params


def _outcome(pass_dt: int, pass_kw: int) -> str:
    if not pass_dt:
        return OUTCOME_DOCTYPE
    if not pass_kw:
        return OUTCOME_KEYWORD
    return OUTCOME_INCLUDED


def build_audit(conn, category_id: int, organisations: Sequence[str],
                document_types: Sequence[str] = (), keywords: Sequence[str] = (),
                keyword_scope: str = "anywhere", batch: int = 1000) -> Dict[str, int]:
    """(Re)build the audit for one category. Requires organisations (the starting
    point). Returns counts by outcome."""
    if not organisations:
        raise ValueError("organisations are required — the audit starts at the organisation filter.")

    conn.execute(f"DELETE FROM category_audit WHERE category_id = {_P}", (category_id,))
    sql, params = _build_select(organisations, document_types, keywords, keyword_scope)
    ts = db.now_iso()
    counters: Dict[str, int] = {OUTCOME_INCLUDED: 0, OUTCOME_DOCTYPE: 0, OUTCOME_KEYWORD: 0, "total": 0}

    ins = (f"INSERT INTO category_audit (category_id, url, outcome, created_at) "
           f"VALUES ({_P},{_P},{_P},{_P})")
    pending = 0
    for row in conn.execute(sql, tuple(params)).fetchall():
        outcome = _outcome(row["pass_dt"], row["pass_kw"])
        conn.execute(ins, (category_id, row["url"], outcome, ts))
        counters[outcome] += 1
        counters["total"] += 1
        pending += 1
        if pending >= batch:
            conn.commit()
            pending = 0
    conn.commit()
    return counters


def audit_summary(conn, category_id: int) -> List[Tuple[str, int]]:
    """[(outcome, count)] for a category, in funnel order."""
    rows = conn.execute(
        f"SELECT outcome, COUNT(*) AS n FROM category_audit WHERE category_id = {_P} "
        f"GROUP BY outcome", (category_id,)).fetchall()
    counts = {r["outcome"]: r["n"] for r in rows}
    order = [OUTCOME_INCLUDED, OUTCOME_DOCTYPE, OUTCOME_KEYWORD]
    return [(o, counts.get(o, 0)) for o in order]


def audit_rows(conn, category_id: int, outcome: Optional[str] = None,
               limit: Optional[int] = None) -> List[dict]:
    """Audit rows for a category, optionally filtered to one outcome."""
    sql = f"SELECT url, outcome FROM category_audit WHERE category_id = {_P}"
    params: list = [category_id]
    if outcome:
        sql += f" AND outcome = {_P}"
        params.append(outcome)
    sql += " ORDER BY url"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def main() -> None:
    from . import categories as cat
    ap = argparse.ArgumentParser(description="Build the per-page funnel audit for a category.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--category", type=int, required=True, help="category id")
    args = ap.parse_args()

    conn = db.connect(args.db)
    db.init_db(conn)
    c = cat.get_category(conn, args.category)
    if not c:
        raise SystemExit(f"No category {args.category}")
    orgs = cat.parse_list(c.get("dept_slugs"))
    counters = build_audit(conn, args.category, orgs,
                           cat.parse_list(c.get("document_type_slugs")),
                           cat.parse_list(c.get("keywords")))
    print(f"Audit for category {args.category} ({c.get('slug')}):")
    for k, v in counters.items():
        print(f"  {k:24} {v}")
    conn.close()


if __name__ == "__main__":
    main()
