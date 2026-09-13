"""Deterministic shortlists — the corpus's reason for existing.

Emit a list of URLs filtered by organisation, document_type and keyword, to hand to
a downstream inference process (Q7). Backend-agnostic: works on SQLite (pilot) and
Postgres (server). Keyword matching is a case-insensitive substring over the raw
`content` JSON for now — the deferred "proper index" (FTS / tsvector) can replace
`_keyword_clause` later without changing the interface.

Examples:
    python3 -m govuk_corpus.shortlist --db data/pilot.db \
        --organisation environment-agency --document-type guidance \
        --keywords slurry,nitrate --match any
    python3 -m govuk_corpus.shortlist --db data/pilot.db --keywords slurry --count
"""
from __future__ import annotations

import argparse
import json
import os
from typing import List, Optional, Sequence, Tuple

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"   # param placeholder for the active backend


def build_query(
    *,
    organisations: Sequence[str] = (),
    document_types: Sequence[str] = (),
    keywords: Sequence[str] = (),
    match: str = "all",                 # all | any  (how to combine keywords)
    include_redirects: bool = False,
    any_status: bool = False,
    count_only: bool = False,
    include_title: bool = False,        # also select c.title (for CSV export)
    limit: Optional[int] = None,
) -> Tuple[str, list]:
    """Build (sql, params). Pure/deterministic, so it is unit-testable."""
    params: list = []
    joins = ""
    where: List[str] = []

    if organisations:
        joins = " JOIN page_organisations po ON po.page_url = c.url"
        placeholders = ",".join([_P] * len(organisations))
        where.append(f"po.organisation_slug IN ({placeholders})")
        params.extend(organisations)

    if document_types:
        placeholders = ",".join([_P] * len(document_types))
        where.append(f"c.document_type IN ({placeholders})")
        params.extend(document_types)

    if keywords:
        joiner = " OR " if match == "any" else " AND "
        clause = joiner.join([f"LOWER(c.content) LIKE {_P}"] * len(keywords))
        where.append(f"({clause})")
        params.extend(f"%{kw.lower()}%" for kw in keywords)

    if not include_redirects:
        where.append("c.is_redirect = 0")
    if not any_status:
        where.append("c.http_status = 200")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    if count_only:
        select = "COUNT(DISTINCT c.url) AS n"
    elif include_title:
        select = "DISTINCT c.url AS url, c.title AS title"
    else:
        select = "DISTINCT c.url AS url"
    sql = f"SELECT {select} FROM content c{joins}{where_sql}"
    if not count_only:
        sql += " ORDER BY c.url"
        if limit:
            sql += f" LIMIT {_P}"
            params.append(limit)
    return sql, params


def shortlist(conn, **kwargs) -> List[str]:
    sql, params = build_query(count_only=False, **kwargs)
    return [r["url"] for r in conn.execute(sql, tuple(params)).fetchall()]


def shortlist_rows(conn, **kwargs) -> List[dict]:
    """Return [{'url':..., 'title':...}] for CSV export."""
    sql, params = build_query(count_only=False, include_title=True, **kwargs)
    return [{"url": r["url"], "title": r["title"]}
            for r in conn.execute(sql, tuple(params)).fetchall()]


def count(conn, **kwargs) -> int:
    kwargs.pop("limit", None)
    sql, params = build_query(count_only=True, **kwargs)
    return conn.execute(sql, tuple(params)).fetchone()["n"]


def _split(values: Optional[List[str]]) -> List[str]:
    """Accept repeated flags and/or comma-separated values."""
    out: List[str] = []
    for v in values or []:
        out.extend(part.strip() for part in v.split(",") if part.strip())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Emit a deterministic URL shortlist.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--organisation", action="append", help="org slug (repeatable / comma-sep)")
    ap.add_argument("--document-type", action="append", help="document_type (repeatable / comma-sep)")
    ap.add_argument("--keywords", action="append", help="keyword(s) (repeatable / comma-sep)")
    ap.add_argument("--match", choices=("all", "any"), default="all", help="combine keywords")
    ap.add_argument("--include-redirects", action="store_true")
    ap.add_argument("--any-status", action="store_true", help="don't restrict to HTTP 200")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--count", action="store_true", help="print the count only")
    ap.add_argument("--format", choices=("txt", "json"), default="txt")
    args = ap.parse_args()

    conn = db.connect(args.db)
    kw = dict(
        organisations=_split(args.organisation),
        document_types=_split(args.document_type),
        keywords=_split(args.keywords),
        match=args.match,
        include_redirects=args.include_redirects,
        any_status=args.any_status,
    )
    if args.count:
        print(count(conn, **kw))
        return
    urls = shortlist(conn, limit=args.limit, **kw)
    if args.format == "json":
        print(json.dumps(urls, indent=2))
    else:
        for u in urls:
            print(u)
    conn.close()


if __name__ == "__main__":
    main()
