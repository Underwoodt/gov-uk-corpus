"""Extract normalised fields + organisations from an /api/content payload.

Deterministic, no model. Mirrors the field extraction proven in the existing
jobs/extract-json-fields.py, but returns a clean dict for upsert rather than
writing SQLite-specific generated columns.
"""
from __future__ import annotations

import re as _re
from typing import Any, Dict, List, Tuple

from .canonical import canonicalise, path_of

_A_HREF = _re.compile(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', _re.I | _re.S)
_TAG = _re.compile(r"<[^>]+>")
_GOVUK = _re.compile(r"^https?://(www\.)?gov\.uk", _re.I)


def _html_blobs(details: Dict[str, Any]) -> List[str]:
    """The HTML bodies of a content payload — top-level body, each part, and html attachments.
    A body may be a plain HTML string or a list of {content_type, content} govspeak/html pairs."""
    out: List[str] = []

    def add(b):
        if isinstance(b, str):
            out.append(b)
        elif isinstance(b, list):
            for it in b:
                if isinstance(it, dict) and str(it.get("content_type", "")).endswith("html"):
                    out.append(it.get("content") or "")

    details = details or {}
    add(details.get("body"))
    for part in details.get("parts") or []:
        add(part.get("body"))
    for att in details.get("attachments") or []:
        if att.get("attachment_type") == "html":
            add(att.get("body"))
    return out


def page_body_links(payload: Dict[str, Any], limit: int = 500) -> List[Dict[str, Any]]:
    """Every hyperlink in a page's body/parts, de-duplicated: [{href, text, external}].
    Relative gov.uk links are expanded to absolute; anchors/mailto/tel/js are skipped."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for blob in _html_blobs(payload.get("details") or {}):
        for m in _A_HREF.finditer(blob or ""):
            href = (m.group(1) or "").strip()
            if not href or href[0] in "#?" or href.lower().startswith(("mailto:", "tel:", "javascript:")):
                continue
            full = "https://www.gov.uk" + href if href.startswith("/") else href
            if full in seen:
                continue
            seen.add(full)
            text = _TAG.sub("", m.group(2) or "").strip()
            out.append({"href": full, "text": (text or full)[:200], "external": not _GOVUK.match(full)})
            if len(out) >= limit:
                return out
    return out

_ORG_BASE = "/government/organisations/"


def _slug_from_base_path(base_path: str) -> str:
    # The slug is the LAST path element. Some orgs sit under a nested base_path
    # (e.g. /government/organisations/courts-tribunals/employment-tribunal), and
    # we want just "employment-tribunal", not "courts-tribunals/employment-tribunal".
    if base_path and base_path.startswith(_ORG_BASE):
        rest = base_path[len(_ORG_BASE):].strip("/")
    else:
        rest = (base_path or "").strip("/")
    return rest.rsplit("/", 1)[-1]


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
        "content_id": payload.get("content_id") or None,   # GOV.UK's real unique id (many urls -> one)
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
