"""Organisation registry + hierarchy.

Imports the GOV.UK search-API organisation aggregate (organisations.json:
`aggregates.organisations.options[].value`) into two tables:

  organisations           — one row per org (slug, title, acronym, type, state, …)
  organisation_hierarchy  — parent_slug -> child_slug edges

so selection can expand a chosen organisation to its child departments (and all
descendants), and resolve a page's organisation to its parent(s). Slugs match
`page_organisations.organisation_slug` (the last path segment), so the corpus
joins straight onto this registry.

    python3 -m govuk_corpus.orgs --json reference/organisations.json      # import
    python3 -m govuk_corpus.orgs --children ministry-of-justice --recursive
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Set

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

_ORG_COLS = ("slug", "title", "acronym", "content_id",
             "org_type", "org_state", "brand", "analytics_identifier")


def _slug_of(value: Dict[str, Any]) -> Optional[str]:
    """Prefer the explicit slug; fall back to the last segment of `link`."""
    slug = value.get("slug")
    if slug:
        return slug
    link = value.get("link") or ""
    return link.rsplit("/", 1)[-1] or None


def _options(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (payload.get("aggregates", {})
                   .get("organisations", {})
                   .get("options", []))


def import_organisations(conn, path: str) -> Dict[str, int]:
    """Load organisations + hierarchy from the aggregate JSON (full snapshot reload)."""
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    options = _options(payload)

    orgs: Dict[str, tuple] = {}
    edges: Set[tuple] = set()
    for opt in options:
        v = opt.get("value") or {}
        slug = _slug_of(v)
        if not slug:
            continue
        orgs[slug] = (slug, v.get("title"), v.get("acronym"), v.get("content_id"),
                      v.get("organisation_type"), v.get("organisation_state"),
                      v.get("organisation_brand"), v.get("analytics_identifier"))
        # Build edges from BOTH directions so the hierarchy is complete even when
        # one side omits the relationship.
        for child in v.get("child_organisations") or []:
            edges.add((slug, child))
        for parent in v.get("parent_organisations") or []:
            edges.add((parent, slug))

    conn.execute("DELETE FROM organisation_hierarchy")
    conn.execute("DELETE FROM organisations")
    ph = ",".join([_P] * len(_ORG_COLS))
    org_sql = f"INSERT INTO organisations ({','.join(_ORG_COLS)}) VALUES ({ph})"
    for row in orgs.values():
        conn.execute(org_sql, row)
    edge_sql = f"INSERT INTO organisation_hierarchy (parent_slug, child_slug) VALUES ({_P},{_P})"
    for edge in edges:
        conn.execute(edge_sql, edge)
    conn.commit()
    return {"options": len(options), "organisations": len(orgs), "edges": len(edges)}


# ---- lookups -------------------------------------------------------------
def get_org(conn, slug: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(f"SELECT * FROM organisations WHERE slug={_P}", (slug,)).fetchone()
    return dict(row) if row else None


def all_orgs(conn) -> List[Dict[str, Any]]:
    """Every organisation as {slug, title}, ordered by title — for the org multi-select
    (the UI shows the title, the category stores the slug)."""
    rows = conn.execute(
        "SELECT slug, COALESCE(title, slug) AS title FROM organisations "
        "ORDER BY LOWER(COALESCE(title, slug))").fetchall()
    return [dict(r) for r in rows]


import re as _re

# Function/noise words that would over-match (generic org words match nearly everything).
_SEARCH_STOP = {
    "the", "of", "and", "for", "a", "an", "to", "in", "on", "with", "from", "that", "this",
    "have", "has", "you", "your", "our", "all", "any", "pages", "page", "step", "steps",
    "guide", "guides", "related", "about", "publish", "published", "document", "documents",
    "agency", "agencies", "department", "departments", "office", "offices", "service",
    "services", "authority", "board", "committee", "council", "team", "group", "unit",
}


def search(conn, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Organisations whose slug, title or acronym matches a word in `query`. Broad, so it
    can recommend the REAL slugs when a user types organisation names or acronyms that
    aren't exact slugs. Returns [{slug, title}], shortest slugs first, de-duplicated."""
    tokens = [t for t in _re.findall(r"[a-z0-9][a-z0-9&\-]+", (query or "").lower())
              if t not in _SEARCH_STOP]
    if not tokens:
        return []
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for t in tokens:
        if len(t) >= 4:      # slug/title substring OR exact acronym
            sql = (f"SELECT slug, title FROM organisations WHERE lower(slug) LIKE {_P} "
                   f"OR lower(title) LIKE {_P} OR lower(acronym) = {_P} "
                   f"ORDER BY length(slug) LIMIT {_P}")
            params = (f"%{t}%", f"%{t}%", t, limit)
        else:                # short token (e.g. an acronym like EA): exact acronym only
            sql = (f"SELECT slug, title FROM organisations WHERE lower(acronym) = {_P} "
                   f"ORDER BY length(slug) LIMIT {_P}")
            params = (t, limit)
        for row in conn.execute(sql, params).fetchall():
            d = dict(row)
            if d["slug"] not in seen:
                seen.add(d["slug"])
                out.append({"slug": d["slug"], "title": d.get("title")})
        if len(out) >= limit:
            break
    return out[:limit]


def children(conn, slug: str) -> List[str]:
    """Direct child slugs of `slug`."""
    rows = conn.execute(
        f"SELECT child_slug FROM organisation_hierarchy WHERE parent_slug={_P} "
        f"ORDER BY child_slug", (slug,)).fetchall()
    return [r["child_slug"] for r in rows]


def parents(conn, slug: str) -> List[str]:
    """Direct parent slugs of `slug`."""
    rows = conn.execute(
        f"SELECT parent_slug FROM organisation_hierarchy WHERE child_slug={_P} "
        f"ORDER BY parent_slug", (slug,)).fetchall()
    return [r["parent_slug"] for r in rows]


def descendants(conn, slug: str) -> List[str]:
    """All descendant slugs (recursive), excluding `slug` itself. Works on SQLite
    and Postgres (both support WITH RECURSIVE)."""
    sql = (
        "WITH RECURSIVE d(slug) AS ("
        f"  SELECT child_slug FROM organisation_hierarchy WHERE parent_slug={_P} "
        "  UNION "
        "  SELECT h.child_slug FROM organisation_hierarchy h JOIN d ON h.parent_slug = d.slug"
        ") SELECT DISTINCT slug FROM d ORDER BY slug")
    return [r["slug"] for r in conn.execute(sql, (slug,)).fetchall()]


def expand_with_children(conn, slugs, recursive: bool = True) -> List[str]:
    """Given selected org slugs, return them plus their child departments — the set
    to filter the corpus by. `recursive=True` includes all descendants."""
    out: Set[str] = set()
    for s in slugs:
        out.add(s)
        out.update(descendants(conn, s) if recursive else children(conn, s))
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Import / query the organisation hierarchy.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--json", default="reference/organisations.json",
                    help="path to the GOV.UK organisations aggregate JSON")
    ap.add_argument("--children", metavar="SLUG", help="print child departments of SLUG")
    ap.add_argument("--parents", metavar="SLUG", help="print parent(s) of SLUG")
    ap.add_argument("--recursive", action="store_true", help="with --children: all descendants")
    args = ap.parse_args()

    conn = db.connect(args.db)
    db.init_db(conn)

    if args.children:
        kids = descendants(conn, args.children) if args.recursive else children(conn, args.children)
        for k in kids:
            print(k)
    elif args.parents:
        for p in parents(conn, args.parents):
            print(p)
    else:
        counters = import_organisations(conn, args.json)
        print("Imported organisations. Counters:")
        for k, v in counters.items():
            print(f"  {k:14} {v}")
    conn.close()


if __name__ == "__main__":
    main()
