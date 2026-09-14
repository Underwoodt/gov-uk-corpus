"""Distinct filter values for the UI pickers (organisations, page types).

Reads the live corpus so the Create/Edit form offers the organisations and
document types that actually exist. Backend-agnostic.
"""
from __future__ import annotations

from typing import List, Tuple

from .backend import db


def _prettify(slug: str) -> str:
    """environment-agency -> Environment Agency; guidance -> Guidance;
    detailed_guide -> Detailed guide (first word capitalised, rest lower)."""
    words = slug.replace("_", " ").replace("-", " ").split()
    if not words:
        return slug
    return " ".join([words[0].capitalize()] + [w.lower() for w in words[1:]])


def organisations(conn) -> List[Tuple[str, str]]:
    """[(slug, display_name)] for every organisation present in the corpus."""
    rows = conn.execute(
        "SELECT DISTINCT organisation_slug FROM page_organisations "
        "WHERE organisation_slug IS NOT NULL AND organisation_slug <> '' "
        "ORDER BY organisation_slug"
    ).fetchall()
    slugs = [r["organisation_slug"] for r in rows]
    return [(s, _prettify(s)) for s in slugs]


def document_types(conn) -> List[Tuple[str, str]]:
    """[(value, display_name)] for every document_type present in the corpus."""
    rows = conn.execute(
        "SELECT document_type, COUNT(*) AS n FROM content "
        "WHERE document_type IS NOT NULL AND document_type <> '' "
        "AND is_redirect = 0 AND content_hash IS NOT NULL "
        "GROUP BY document_type ORDER BY n DESC"
    ).fetchall()
    return [(r["document_type"], _prettify(r["document_type"])) for r in rows]
