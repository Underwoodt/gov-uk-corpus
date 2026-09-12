"""Extract normalised fields + organisations from an /api/content payload.

Deterministic, no model. Mirrors the field extraction proven in the existing
jobs/extract-json-fields.py, but returns a clean dict for upsert rather than
writing SQLite-specific generated columns.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .canonical import canonicalise

_ORG_BASE = "/government/organisations/"


def _slug_from_base_path(base_path: str) -> str:
    if base_path and base_path.startswith(_ORG_BASE):
        return base_path[len(_ORG_BASE):].strip("/")
    return (base_path or "").strip("/")


def extract_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Core scalar fields for the content row."""
    document_type = payload.get("document_type") or ""
    schema_name = payload.get("schema_name") or ""
    is_redirect = 1 if (document_type == "redirect" or schema_name == "redirect") else 0

    destination_url = None
    redirects = payload.get("redirects") or []
    if redirects:
        dest = redirects[0].get("destination")
        if dest:
            destination_url = canonicalise("https://www.gov.uk" + dest if dest.startswith("/") else dest)

    withdrawn = 1 if payload.get("withdrawn_notice") else 0

    return {
        "document_type": document_type,
        "schema_name": schema_name,
        "title": payload.get("title") or "",
        "description": payload.get("description") or "",
        "first_published_at": payload.get("first_published_at") or "",
        "public_updated_at": payload.get("public_updated_at") or "",
        "withdrawn": withdrawn,
        "is_redirect": is_redirect,
        "destination_url": destination_url,
    }


def extract_organisations(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """Primary + related organisations, de-duplicated by (content_id, role)."""
    links = payload.get("links") or {}
    out: List[Dict[str, str]] = []
    seen = set()

    def add(items, role):
        for it in items or []:
            cid = it.get("content_id")
            slug = _slug_from_base_path(it.get("base_path", ""))
            key = (cid, role)
            if key in seen:
                continue
            seen.add(key)
            out.append({"content_id": cid, "slug": slug, "role": role})

    add(links.get("primary_publishing_organisation"), "primary")
    add(links.get("organisations"), "related")
    return out
