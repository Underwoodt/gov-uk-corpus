"""Export a category and everything needed to reproduce its result, and import it
into another environment (typically prod -> a test DB, to debug a category).

The bundle is a single JSON object. It carries the category definition, its runs and
per-page AI results, the persisted shortlist membership, the deterministic audit, and
the corpus rows those reference: **metadata-only** content (no raw /api/content JSON),
page_organisations, and the organisations + hierarchy needed for org filtering and
child-org expansion. No user rows and no password hashes ever leave the source — on
import the category is reassigned to the importing user (see ``owner_email``).

Backend-agnostic: reads/writes through the active ``db`` layer, so it runs prod
Postgres -> test Postgres or -> local SQLite.

    # on live
    python3 -m govuk_corpus.category_transfer export 42 > cat-42.json
    # in the test env
    python3 -m govuk_corpus.category_transfer import cat-42.json --owner me@test.local
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from typing import Optional

from . import categories as cat
from .backend import db

_P = "%s" if db.__name__.endswith("db_pg") else "?"
BUNDLE_FORMAT = "govuk-corpus-category-bundle"
BUNDLE_VERSION = 1

# Content columns we carry — everything except the heavy `content` JSON blob and the
# generated `search_tsv` (which the target recomputes on insert from title/description/
# search_text, so keyword search still reproduces).
_CONTENT_COLS = [
    "url", "content_id", "content_hash", "source", "document_type", "parent_document_type",
    "schema_name", "title", "description", "first_published_at", "public_updated_at",
    "withdrawn", "is_redirect", "destination_url", "http_status", "sitemap_lastmod",
    "search_text", "reading_age", "gds_english_score", "gds_findings", "gds_checks",
    "gds_stars", "first_seen_run", "last_seen_run", "last_changed_run",
    "first_seen_at", "last_seen_at", "last_changed_at",
]


def _rows(conn, sql, params=()):
    try:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    except Exception:
        return []


def _chunks(seq, n=500):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# --------------------------------------------------------------------------- export

def export_bundle(conn, cid: int) -> Optional[dict]:
    """Gather a self-contained bundle for one category, or None if it doesn't exist."""
    category = cat.get_category(conn, cid)
    if not category:
        return None

    runs = _rows(conn, f"SELECT * FROM evaluation_runs WHERE category_id = {_P} "
                       f"ORDER BY started_at", (cid,))
    run_ids = [r["run_id"] for r in runs]
    results = []
    for chunk in _chunks(run_ids):
        ph = ",".join([_P] * len(chunk))
        results += _rows(conn, f"SELECT * FROM evaluation_results WHERE run_id IN ({ph})", chunk)

    membership = _rows(conn, f"SELECT * FROM category_shortlist_pages WHERE category_id = {_P}", (cid,))
    counts = _rows(conn, f"SELECT * FROM category_page_counts WHERE category_id = {_P}", (cid,))
    audit = _rows(conn, f"SELECT * FROM category_audit WHERE category_id = {_P}", (cid,))

    # The corpus rows the result touches: membership representatives + every url named
    # by a run's results or the audit, so runs/results/audit resolve to real pages.
    urls = {m["url"] for m in membership if m.get("url")}
    urls |= {r["url"] for r in results if r.get("url")}
    urls |= {a["url"] for a in audit if a.get("url")}

    content, page_orgs = [], []
    org_slugs = set(cat.parse_list(category.get("dept_slugs")))
    cols = ", ".join(_CONTENT_COLS)
    for chunk in _chunks(list(urls)):
        ph = ",".join([_P] * len(chunk))
        content += _rows(conn, f"SELECT {cols} FROM content WHERE url IN ({ph})", chunk)
        for po in _rows(conn, f"SELECT * FROM page_organisations WHERE page_url IN ({ph})", chunk):
            page_orgs.append(po)
            if po.get("organisation_slug"):
                org_slugs.add(po["organisation_slug"])

    # Organisations + hierarchy so org filtering and child-org expansion reproduce.
    hierarchy, organisations = [], []
    if org_slugs:
        sl = list(org_slugs)
        ph = ",".join([_P] * len(sl))
        hierarchy = _rows(conn, f"SELECT * FROM organisation_hierarchy "
                                f"WHERE parent_slug IN ({ph}) OR child_slug IN ({ph})", sl + sl)
        for h in hierarchy:
            org_slugs.update((h.get("parent_slug"), h.get("child_slug")))
        sl = [s for s in org_slugs if s]
        ph = ",".join([_P] * len(sl))
        organisations = _rows(conn, f"SELECT * FROM organisations WHERE slug IN ({ph})", sl)

    return {
        "meta": {
            "format": BUNDLE_FORMAT, "version": BUNDLE_VERSION, "content": "metadata",
            "exported_at": cat.now_iso(), "source_category_id": cid,
            "slug": category.get("slug"),
        },
        "category": category,
        "counts": counts,
        "membership": membership,
        "runs": runs,
        "results": results,
        "audit": audit,
        "organisations": organisations,
        "organisation_hierarchy": hierarchy,
        "content": content,
        "page_organisations": page_orgs,
    }


# --------------------------------------------------------------------------- import

def _insert(conn, table: str, row: dict, conflict=None, update=False) -> None:
    """Insert one row (dict). `conflict` = PK columns for upsert; `update` merges the
    rest on conflict, else does nothing. ON CONFLICT works on both SQLite and Postgres."""
    cols = list(row.keys())
    ph = ",".join([_P] * len(cols))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph})"
    if conflict:
        others = [c for c in cols if c not in conflict]
        if update and others:
            sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in others)
            sql += f" ON CONFLICT ({', '.join(conflict)}) DO UPDATE SET {sets}"
        else:
            sql += f" ON CONFLICT ({', '.join(conflict)}) DO NOTHING"
    conn.execute(sql, tuple(row[c] for c in cols))


def import_bundle(conn, bundle: dict, owner_email: Optional[str] = None) -> dict:
    """Load a bundle into this database. Preserves the source ids (so runs/results/
    membership line up) and cleanly replaces any existing rows for that category, so
    re-importing the same category is idempotent. If owner_email is given, the category
    is reassigned to that user (the importer) — no source user/credential is involved."""
    if (bundle.get("meta") or {}).get("format") != BUNDLE_FORMAT:
        raise ValueError("Not a category bundle (bad meta.format).")
    category = dict(bundle["category"])
    cid = category["id"]
    if owner_email:
        category["owner_email"] = owner_email

    # Clean replace everything scoped to this category id.
    conn.execute(f"DELETE FROM evaluation_results WHERE run_id IN "
                 f"(SELECT run_id FROM evaluation_runs WHERE category_id = {_P})", (cid,))
    for t in ("evaluation_runs", "category_shortlist_pages", "category_page_counts",
              "category_audit"):
        conn.execute(f"DELETE FROM {t} WHERE category_id = {_P}", (cid,))

    # Corpus-shared rows: upsert (refresh metadata / never clobber other categories' data).
    for r in bundle.get("organisations", []):
        _insert(conn, "organisations", r, conflict=["slug"], update=True)
    for r in bundle.get("organisation_hierarchy", []):
        _insert(conn, "organisation_hierarchy", r, conflict=["parent_slug", "child_slug"])
    for r in bundle.get("content", []):
        _insert(conn, "content", r, conflict=["url"], update=True)
    for r in bundle.get("page_organisations", []):
        _insert(conn, "page_organisations", r,
                conflict=["page_url", "organisation_content_id", "role"])

    # The category and its own rows.
    _insert(conn, "categories", category, conflict=["id"], update=True)
    for r in bundle.get("counts", []):
        _insert(conn, "category_page_counts", r, conflict=["category_id"], update=True)
    for r in bundle.get("membership", []):
        _insert(conn, "category_shortlist_pages", r)
    for r in bundle.get("runs", []):
        _insert(conn, "evaluation_runs", r, conflict=["run_id"], update=True)
    for r in bundle.get("results", []):
        _insert(conn, "evaluation_results", r)
    for r in bundle.get("audit", []):
        _insert(conn, "category_audit", r)
    conn.commit()

    return {
        "category_id": cid, "slug": category.get("slug"), "owner_email": category.get("owner_email"),
        "content": len(bundle.get("content", [])), "page_organisations": len(bundle.get("page_organisations", [])),
        "organisations": len(bundle.get("organisations", [])), "runs": len(bundle.get("runs", [])),
        "results": len(bundle.get("results", [])), "membership": len(bundle.get("membership", [])),
        "audit": len(bundle.get("audit", [])),
    }


def dumps(bundle: dict) -> str:
    return json.dumps(bundle, default=str, ensure_ascii=False)


def loads(text: str) -> dict:
    return json.loads(text)


# --------------------------------------------------------------------------- CLI

def _open_maybe_gz(path: str):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export/import a category bundle between environments.")
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("target", help="category id (export) or bundle file path, .json or .json.gz (import)")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--owner", help="import: reassign the category to this owner email (the importer)")
    args = ap.parse_args()

    conn = db.connect(args.db)
    db.init_db(conn)
    if args.action == "export":
        bundle = export_bundle(conn, int(args.target))
        if bundle is None:
            sys.exit(f"No category with id {args.target}")
        sys.stdout.write(dumps(bundle))
    else:
        with _open_maybe_gz(args.target) as fh:
            bundle = loads(fh.read())
        summary = import_bundle(conn, bundle, owner_email=args.owner)
        print("Imported category %(category_id)s (%(slug)s), owner %(owner_email)s:" % summary)
        for k in ("content", "page_organisations", "organisations", "runs", "results", "membership", "audit"):
            print(f"  {k:20} {summary[k]}")
    conn.close()


if __name__ == "__main__":
    main()
