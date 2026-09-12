"""Extract normalised fields + organisations from an /api/content payload.

Deterministic, no model. Mirrors the field extraction proven in the existing
jobs/extract-json-fields.py, but returns a clean dict for upsert rather than
writing SQLite-specific generated columns.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .canonical import canonicalise, path_of

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


def extract_child_links(payload: Dict[str, Any], parent_url: str) -> Tuple[List[Tuple[str, str]], int]:
    """Child/attachment PAGES referenced by a parent page.

    Returns (child_links, binary_count) where child_links is a list of
    (canonical_child_url, relation) with relation in {child, part, attachment}.
    `binary_count` is the number of non-page file attachments (PDF/spreadsheet/...)
    — deferred to a future binary_attachments table, counted for visibility only.

    Sources:
      - links.children            -> HTML attachment / child pages (relation "child")
      - details.parts[].slug      -> multi-part guide sections (relation "part")
      - details.attachments html  -> inline HTML attachments (relation "attachment")
    """
    base = payload.get("base_path") or path_of(parent_url) or ""
    links = payload.get("links") or {}
    details = payload.get("details") or {}

    raw: List[Tuple[str, str]] = []
    for it in links.get("children") or []:
        bp = it.get("base_path")
        if bp:
            raw.append((bp, "child"))
    for part in details.get("parts") or []:
        slug = part.get("slug")
        if slug and base:
            raw.append((base.rstrip("/") + "/" + slug, "part"))

    binary_count = 0
    for att in details.get("attachments") or []:
        if att.get("attachment_type") == "html" and att.get("url"):
            raw.append((att["url"], "attachment"))
        elif att.get("attachment_type") == "file":
            binary_count += 1

    out: List[Tuple[str, str]] = []
    seen = set()
    for ref, relation in raw:
        target = ref if ref.startswith("http") else "https://www.gov.uk" + ref
        cu = canonicalise(target)
        if cu is None or cu == parent_url:  # drop external/bad and self-references
            continue
        key = (cu, relation)
        if key in seen:
            continue
        seen.add(key)
        out.append((cu, relation))
    return out, binary_count
