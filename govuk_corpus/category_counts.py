"""Precomputed per-category "input shortlist" size.

The Categories list used to run a live corpus ``COUNT(*)`` for every category on
every page load (organisations + document types, no keywords) — an O(categories)
fan-out over the ~877k-row ``content`` table that was slow on a cold cache. Instead
we store one number per category in ``category_page_counts`` and refresh it:

  * as the tidy-up phase of the nightly corpus cycle (``run_all.run_cycle``), and
  * on every category create/edit (``refresh_one``, cheap — a single count).

The list page then reads a stored value in one query via ``get_counts``.

Backend-agnostic: uses ``shortlist.count`` and the active ``db`` placeholder, so it
runs on both SQLite (pilot/tests) and Postgres (server).
"""
from __future__ import annotations

from typing import Dict, Optional

from . import categories as cat
from . import shortlist
from .backend import db

# ``ON CONFLICT ... DO UPDATE`` works on both SQLite and Postgres (mirrors
# settings.py); ``_P`` is the active parameter placeholder.
_P = "%s" if db.__name__.endswith("db_pg") else "?"


def _count_for(conn, category: dict) -> int:
    """Input-shortlist size for one category: organisations + document types, no
    keywords (matches what the list column has always shown)."""
    return shortlist.count(
        conn,
        organisations=cat.parse_list(category.get("dept_slugs")),
        document_types=cat.parse_list(category.get("document_type_slugs")),
        keywords=[],
    )


def _store(conn, category_id: int, pages_kept: int) -> None:
    conn.execute(
        f"INSERT INTO category_page_counts (category_id, pages_kept, computed_at) "
        f"VALUES ({_P}, {_P}, {_P}) "
        f"ON CONFLICT (category_id) DO UPDATE SET "
        f"pages_kept = EXCLUDED.pages_kept, computed_at = EXCLUDED.computed_at",
        (category_id, pages_kept, cat.now_iso()),
    )


def refresh_one(conn, cid: int) -> Optional[int]:
    """Recompute and store the count for a single category. Returns the count, or
    None if the category no longer exists. Best-effort: never raises."""
    try:
        category = cat.get_category(conn, cid)
        if not category:
            return None
        n = _count_for(conn, category)
        _store(conn, cid, n)
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        return None


def refresh_all(conn) -> Dict[str, int]:
    """Recompute the stored count for every category and prune rows for categories
    that no longer exist. Returns a summary dict. Used by the nightly tidy-up."""
    rows = cat.list_categories(conn)
    ids = [int(r["id"]) for r in rows]
    updated = 0
    for r in rows:
        try:
            _store(conn, int(r["id"]), _count_for(conn, r))
            updated += 1
        except Exception:
            conn.rollback()  # skip the bad row, keep going
    # prune counts for deleted categories
    if ids:
        ph = ",".join([_P] * len(ids))
        conn.execute(f"DELETE FROM category_page_counts WHERE category_id NOT IN ({ph})", tuple(ids))
    else:
        conn.execute("DELETE FROM category_page_counts")
    conn.commit()
    return {"categories": len(ids), "updated": updated}


def get_counts(conn) -> Dict[int, dict]:
    """All stored counts as ``{category_id: {"pages_kept", "computed_at"}}`` in one
    read, for the Categories list. Missing categories simply aren't in the map."""
    try:
        rows = conn.execute(
            "SELECT category_id, pages_kept, computed_at FROM category_page_counts"
        ).fetchall()
    except Exception:
        return {}
    return {int(r["category_id"]): {"pages_kept": r["pages_kept"], "computed_at": r["computed_at"]}
            for r in rows}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Refresh precomputed category page counts.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    args = ap.parse_args()
    conn = db.connect(args.db)
    db.init_db(conn)
    summary = refresh_all(conn)
    print(f"Category counts refreshed: {summary['updated']}/{summary['categories']} categories.")
    conn.close()


if __name__ == "__main__":
    main()
