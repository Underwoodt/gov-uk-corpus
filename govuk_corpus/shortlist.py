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
# Byte size of the stored body text (octet_length on PG, length on SQLite).
_SIZE_EXPR = "octet_length(c.search_text)" if _IS_PG else "length(c.search_text)"


def _keyword_clause(keywords, match, is_pg):
    """(sql_fragment, params) for keyword matching over title+description.

    Postgres: GIN-indexed full-text (search_tsv @@ tsquery), stemmed via 'english'.
    SQLite (pilot): case-insensitive LIKE over title+description.
    `match`: 'any' → OR the keywords, 'all' → AND them.
    """
    if is_pg:
        op = " || " if match == "any" else " && "
        tq = op.join(["plainto_tsquery('english', %s)"] * len(keywords))
        return f"c.search_tsv @@ ({tq})", list(keywords)
    op = " OR " if match == "any" else " AND "
    field = ("LOWER(COALESCE(c.title,'') || ' ' || COALESCE(c.description,'') "
             "|| ' ' || COALESCE(c.search_text,''))")
    like = op.join([f"{field} LIKE ?"] * len(keywords))
    return f"({like})", [f"%{kw.lower()}%" for kw in keywords]


def build_query(
    *,
    organisations: Sequence[str] = (),
    document_types: Sequence[str] = (),
    keywords: Sequence[str] = (),
    match: str = "all",                 # all | any  (how to combine keywords)
    include_redirects: bool = False,
    include_unfetched: bool = False,    # include rows we have no body for (content_hash NULL)
    count_only: bool = False,
    include_title: bool = False,        # also select c.title (for CSV export)
    detail: bool = False,               # url + title + size + last-updated (for the results table)
    select_expr: Optional[str] = None,  # explicit SELECT list (for custom exports)
    extra_where: Optional[str] = None,  # extra AND clause (e.g. exclude evaluated urls)
    extra_params: Sequence = (),        # params for extra_where
    limit: Optional[int] = None,
    offset: Optional[int] = None,       # skip N rows (pagination)
) -> Tuple[str, list]:
    """Build (sql, params). Pure/deterministic, so it is unit-testable."""
    params: list = []
    where: List[str] = []

    if organisations:
        # EXISTS (a semi-join, not a JOIN) so a page with several matching org rows is
        # counted once without a DISTINCT. With fresh stats + a warm cache Postgres plans
        # this org-first off idx_page_orgs_slug and it is the fastest form we've measured
        # for both counts and the shortlist SELECT — no CTE needed.
        placeholders = ",".join([_P] * len(organisations))
        where.append(
            f"EXISTS (SELECT 1 FROM page_organisations po "
            f"WHERE po.page_url = c.url AND po.organisation_slug IN ({placeholders}))")
        params.extend(organisations)

    if document_types:
        where.append(doctype_clause(document_types))
        params.extend(document_types)

    if keywords:
        clause, kw_params = _keyword_clause(keywords, match, _IS_PG)
        where.append(clause)
        params.extend(kw_params)

    if extra_where:
        where.append(extra_where)
        params.extend(extra_params)

    if not include_redirects:
        where.append("c.is_redirect = 0")
    if not include_unfetched:
        # "usable" = we actually have the page body. http_status is unreliable in the
        # migrated data (old crawler stored NULL status for many fully-fetched pages),
        # so gate on content presence, matching the dashboard's "fetched" definition.
        where.append("c.content_hash IS NOT NULL")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    # c.url is the primary key and every filter is now a predicate on content c
    # (organisations via EXISTS), so there is no row fan-out and DISTINCT is unneeded.
    if count_only:
        select = "COUNT(*) AS n"
    elif select_expr:
        select = select_expr
    elif detail:
        select = (f"c.url AS url, c.title AS title, {_SIZE_EXPR} AS size_bytes, "
                  "c.public_updated_at AS updated")
    elif include_title:
        select = "c.url AS url, c.title AS title"
    else:
        select = "c.url AS url"
    sql = f"SELECT {select} FROM content c{where_sql}"
    if not count_only:
        sql += " ORDER BY c.url"
        if limit:
            sql += f" LIMIT {_P}"
            params.append(limit)
        if offset:
            sql += f" OFFSET {_P}"
            params.append(offset)
    return sql, params


def _quote_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"   # escape single quotes


def interpolate_sql(sql: str, params, is_pg: bool = _IS_PG) -> str:
    """Substitute params into placeholders, quoting values — for a readable, copy-
    pasteable query. (The executed query still uses safe parameter binding; this is
    display only.)"""
    placeholder = "%s" if is_pg else "?"
    segments = sql.split(placeholder)
    out = [segments[0]]
    for i, seg in enumerate(segments[1:]):
        out.append(_quote_literal(params[i]) if i < len(params) else placeholder)
        out.append(seg)
    return "".join(out)


_SQL_BREAK_KEYWORDS = (
    "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "JOIN", "FROM", "WHERE",
    "GROUP BY", "ORDER BY", "LIMIT", "HAVING", " AND ", " OR ",
)


def pretty_sql(sql: str) -> str:
    """Reindent SQL for display. Uses sqlparse when available; otherwise falls back
    to a dependency-free reformat (newline before the major clause keywords) so the
    query never collapses onto a single line when sqlparse is missing."""
    try:
        import sqlparse
        return sqlparse.format(sql, reindent=True, keyword_case="upper")
    except Exception:
        pass
    out = " ".join(sql.split())   # collapse existing whitespace first
    for kw in _SQL_BREAK_KEYWORDS:
        bare = kw.strip()
        out = out.replace(f" {bare} ", f"\n{bare} ")
    return out.strip()


def shortlist(conn, **kwargs) -> List[str]:
    sql, params = build_query(count_only=False, **kwargs)
    return [r["url"] for r in conn.execute(sql, tuple(params)).fetchall()]


def shortlist_rows(conn, **kwargs) -> List[dict]:
    """Return [{'url':..., 'title':...}] for CSV export."""
    sql, params = build_query(count_only=False, include_title=True, **kwargs)
    return [{"url": r["url"], "title": r["title"]}
            for r in conn.execute(sql, tuple(params)).fetchall()]


def detail_rows(conn, **kwargs) -> List[dict]:
    """Rows for the results table: url, title, size_bytes, updated (last public update)."""
    sql, params = build_query(detail=True, **kwargs)
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


# Parent document type: the materialised content.parent_document_type column when it has
# been backfilled (fast, indexable), otherwise dug live out of the content JSON at
# links.parent[].document_type (e.g. an html_publication's real type is its parent
# publication's type). The live path is guarded so a malformed/empty content value yields
# NULL instead of erroring the whole export, and tolerant of links.parent being an array
# or a single object. COALESCE short-circuits, so backfilled rows skip the JSON cast.
if _IS_PG:
    # NB: the literal '%' in the LIKE must be doubled to '%%' — psycopg parses the query
    # for parameter placeholders and treats a bare '%' as an (invalid) placeholder.
    _PARENT_DT_JSON = (
        "CASE WHEN c.content IS NOT NULL AND btrim(c.content) LIKE '{%%' THEN "
        "COALESCE(c.content::jsonb #>> '{links,parent,0,document_type}', "
        "c.content::jsonb #>> '{links,parent,document_type}') END")
else:
    _PARENT_DT_JSON = (
        "CASE WHEN json_valid(c.content) THEN "
        "COALESCE(json_extract(c.content, '$.links.parent[0].document_type'), "
        "json_extract(c.content, '$.links.parent.document_type')) END")
_PARENT_DT_EXPR = f"COALESCE(c.parent_document_type, {_PARENT_DT_JSON})"

# All organisation slugs linked to the page, comma-joined (correlated subquery).
if _IS_PG:
    _ORGS_EXPR = ("(SELECT string_agg(DISTINCT po.organisation_slug, ', ') "
                  "FROM page_organisations po WHERE po.page_url = c.url)")
else:
    _ORGS_EXPR = ("(SELECT group_concat(DISTINCT po.organisation_slug) "
                  "FROM page_organisations po WHERE po.page_url = c.url)")
# The page's primary-role organisation (its publishing organisation), if recorded.
_PRIMARY_ORG_EXPR = ("(SELECT po.organisation_slug FROM page_organisations po "
                     "WHERE po.page_url = c.url AND po.role = 'primary' LIMIT 1)")

# Effective document type: for html_publication pages, the parent publication's type.
_EFF_DOCTYPE_EXPR = (f"CASE WHEN c.document_type = 'html_publication' "
                     f"THEN COALESCE(NULLIF(c.parent_document_type, ''), {_PARENT_DT_JSON}, c.document_type) "
                     f"ELSE c.document_type END")
# Public alias: the document-type filter matches on this so an html_publication (the
# content body of a publication) is selected by its PARENT publication's type. Shared by
# audit.py / audit_stats.py so the funnel, audit log and dashboard all agree.
EFFECTIVE_DOCTYPE_EXPR = _EFF_DOCTYPE_EXPR


def doctype_clause(document_types: Sequence[str]) -> str:
    """WHERE fragment for a document-type filter. Matches on the EFFECTIVE type, so an
    html_publication (a publication's content body) is selected by its PARENT's type —
    e.g. picking "guidance" also keeps html_publications whose parent is guidance. If
    "html_publication" is itself selected, every html_publication still matches too, so
    listing it never silently empties the result. Caller extends params with
    `document_types` once, in order."""
    ph = ",".join([_P] * len(document_types))
    clause = f"{_EFF_DOCTYPE_EXPR} IN ({ph})"
    if "html_publication" in document_types:
        clause = f"({clause} OR c.document_type = 'html_publication')"
    return clause

# Freshness band from public_updated_at (lexical ISO compare against now-relative dates).
if _IS_PG:
    def _ago(days): return f"to_char(now() - interval '{days} days', 'YYYY-MM-DD')"
else:
    def _ago(days): return f"date('now', '-{days} days')"
_LAST_UPDATE_BAND_EXPR = (
    "CASE WHEN c.public_updated_at IS NULL OR c.public_updated_at = '' THEN 'Unknown' "
    f"WHEN c.public_updated_at >= {_ago(30)} THEN '< 1 month' "
    f"WHEN c.public_updated_at >= {_ago(91)} THEN '1-3 months' "
    f"WHEN c.public_updated_at >= {_ago(365)} THEN '3 months-1 year' "
    f"WHEN c.public_updated_at >= {_ago(730)} THEN '1-2 years' "
    "ELSE '> 2 years' END")

# Exportable fields: key -> (SQL expression, human label). 'url' is mandatory.
EXPORT_FIELDS = {
    "url": ("c.url", "URL"),
    "title": ("c.title", "Title"),
    "document_type": ("c.document_type", "Document type"),
    "effective_document_type": (_EFF_DOCTYPE_EXPR, "Document type"),
    "parent_document_type": (_PARENT_DT_EXPR, "Parent document type"),
    "organisations": (_ORGS_EXPR, "Organisations"),
    "primary_org": (_PRIMARY_ORG_EXPR, "Primary publishing organisation"),
    "size": (_SIZE_EXPR, "Size (bytes)"),
    "readability": ("c.reading_age", "Readability (reading age)"),
    "gds_issues": ("c.gds_english_score", "GDS issues (count)"),
    "gds_findings": ("c.gds_findings", "GDS issues (text)"),
    "content": ("c.content", "Content (raw JSON)"),
    "first_published_at": ("c.first_published_at", "First published at"),
    "public_updated_at": ("c.public_updated_at", "Public updated at"),
    "last_update_band": (_LAST_UPDATE_BAND_EXPR, "Last update band"),
}


def export_query(fields: Sequence[str], *, limit: Optional[int] = 100000,
                 offset: Optional[int] = None, **filters) -> Tuple[List[str], str, list]:
    """(ordered field keys, sql, params) for the chosen fields — build without executing,
    so callers can both run it and show the SQL. 'url' is always included first."""
    keys = [f for f in fields if f in EXPORT_FIELDS and f != "url"]
    keys = ["url"] + keys
    select_expr = ", ".join(f"{EXPORT_FIELDS[k][0]} AS {k}" for k in keys)
    sql, params = build_query(select_expr=select_expr, limit=limit, offset=offset, **filters)
    return keys, sql, params


def export_rows(conn, fields: Sequence[str], *, limit: Optional[int] = 100000,
                offset: Optional[int] = None, **filters) -> Tuple[List[str], List[dict]]:
    """(ordered field keys, rows) for the chosen fields. 'url' is always included first."""
    keys, sql, params = export_query(fields, limit=limit, offset=offset, **filters)
    rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    return keys, rows


def selection_funnel(conn, organisations: Sequence[str] = (),
                     document_types: Sequence[str] = (),
                     keywords: Sequence[str] = ()) -> List[Tuple[str, int]]:
    """Progressive narrowing: total → org → document type → keyword.

    Each stage is a fast indexed COUNT (keyword via the GIN full-text index on
    Postgres), so this is cheap even on the full corpus.
    """
    return [
        ("All pages", count(conn)),
        ("After organisation filter", count(conn, organisations=organisations)),
        ("After document-type filter",
         count(conn, organisations=organisations, document_types=document_types)),
        # Always shown as the final total; equals the previous row when no keywords are set.
        ("After keyword filter",
         count(conn, organisations=organisations, document_types=document_types,
               keywords=keywords, match="any")),
    ]


def count(conn, **kwargs) -> int:
    kwargs.pop("limit", None)
    kwargs.pop("offset", None)
    sql, params = build_query(count_only=True, **kwargs)
    return conn.execute(sql, tuple(params)).fetchone()["n"]


def org_breakdown_query(*, organisations: Sequence[str], document_types: Sequence[str] = (),
                        keywords: Sequence[str] = (), match: str = "any",
                        limit: int = 300) -> Tuple[Optional[str], list]:
    """(sql, params) for the per-organisation breakdown, or (None, []) with no orgs."""
    if not organisations:
        return None, []
    where = []
    params: list = []
    ph = ",".join([_P] * len(organisations))
    where.append(f"po.organisation_slug IN ({ph})")
    params.extend(organisations)
    if document_types:
        where.append(doctype_clause(document_types))     # effective type (html_publication -> parent)
        params.extend(document_types)
    if keywords:
        clause, kwp = _keyword_clause(keywords, match, _IS_PG)
        where.append(clause)
        params.extend(kwp)
    where.append("c.is_redirect = 0")
    where.append("c.content_hash IS NOT NULL")
    sql = (f"SELECT po.organisation_slug AS org, COUNT(DISTINCT po.page_url) AS n "
           f"FROM content c JOIN page_organisations po ON po.page_url = c.url "
           f"WHERE {' AND '.join(where)} "
           f"GROUP BY po.organisation_slug ORDER BY n DESC, po.organisation_slug LIMIT {int(limit)}")
    return sql, params


def org_breakdown(conn, *, organisations: Sequence[str], document_types: Sequence[str] = (),
                  keywords: Sequence[str] = (), match: str = "any", limit: int = 300) -> List[dict]:
    """Pages contributed by EACH organisation, within the document-type + keyword filters.
    One GROUP BY over the filtered set. Organisations overlap (a page can have several),
    so the per-org counts don't sum to the shortlist total."""
    sql, params = org_breakdown_query(organisations=organisations, document_types=document_types,
                                      keywords=keywords, match=match, limit=limit)
    if sql is None:
        return []
    return [{"organisation": r["org"], "count": r["n"]}
            for r in conn.execute(sql, tuple(params)).fetchall()]


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
    ap.add_argument("--include-unfetched", action="store_true",
                    help="include pages we have no body for (content_hash NULL)")
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
        include_unfetched=args.include_unfetched,
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
