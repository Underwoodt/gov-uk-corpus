"""Precomputed corpus document-type counts.

A materialised `document_types` table — every `content.document_type` with its page count —
rebuilt in the batch tidy-up (`run_all.run_tidy_up`). The whole-corpus Page Types picker (when
no organisation is chosen) and the save-time document-type validation read from this small
table instead of a live GROUP BY over `content`, which is slow. Org-scoped picker counts stay
live, since they depend on the chosen organisations.

Source query (as specified):
    SELECT document_type, COUNT(*) AS type_count FROM content GROUP BY document_type
(`document_type IS NOT NULL` is added so the slug can be the table's primary key.)
"""
from __future__ import annotations

from typing import List, Set

from . import categories as _cat
from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"


def refresh_document_types(conn) -> int:
    """Recompute the page count per document_type over the whole corpus and replace the
    `document_types` table. One GROUP BY over `content`, so it runs in the batch tidy-up, not
    per request. Returns the number of document types counted."""
    rows = conn.execute(
        "SELECT document_type, COUNT(*) AS type_count FROM content "
        "WHERE document_type IS NOT NULL "
        "GROUP BY document_type").fetchall()
    ts = _cat.now_iso()
    conn.execute("DELETE FROM document_types")
    data = [(dict(r)["document_type"], dict(r)["type_count"]) for r in rows]
    CHUNK = 500
    for i in range(0, len(data), CHUNK):
        batch = data[i:i + CHUNK]
        values = ",".join([f"({_P}, {_P}, {_P})"] * len(batch))
        params: list = []
        for dt, n in batch:
            params.extend((dt, n, ts))
        conn.execute(
            f"INSERT INTO document_types (document_type, type_count, computed_at) VALUES {values}",
            tuple(params))
    conn.commit()
    return len(data)


def all_counts(conn) -> List[dict]:
    """[{type, count}] from the materialised table, largest first. Empty before the first
    refresh — callers then fall back to a live query."""
    rows = conn.execute(
        "SELECT document_type AS dt, type_count AS n FROM document_types "
        "ORDER BY type_count DESC, document_type").fetchall()
    return [{"type": dict(r)["dt"], "count": dict(r)["n"]} for r in rows]


def known_types(conn) -> Set[str]:
    """Every document_type slug in the corpus, from the materialised table (empty before the
    first refresh)."""
    return {dict(r)["dt"] for r in conn.execute(
        "SELECT document_type AS dt FROM document_types").fetchall()}


def computed_at(conn):
    row = conn.execute("SELECT MAX(computed_at) AS t FROM document_types").fetchone()
    return dict(row)["t"] if row else None
