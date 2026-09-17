"""Saved shortlist specs ("categories") — CRUD + validation, backend-agnostic.

A category is a saved query: filter fields (dept_slugs, document_type_slugs,
keywords) that execute against the corpus, plus inference fields
(inclusion/exclusion context, adjudication hints, URL overrides) stored for the
downstream LLM phases. Persisted in the `categories` table on the active backend.

No Streamlit here — pure functions so they can be unit-tested on SQLite.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

# User-editable fields, in storage order.
USER_FIELDS = [
    "slug", "owner_email", "description", "dept_slugs", "include_child_orgs",
    "document_type_slugs", "keywords",
    "inclusion_context", "exclusion_context", "adjudication_hints_keep",
    "adjudication_hints_drop", "extra_guidance_urls", "only_use_extra_guidance_urls",
    "extra_law_urls", "only_use_extra_law_urls",
]
REQUIRED_FIELDS = [
    "owner_email", "description", "dept_slugs", "document_type_slugs", "inclusion_context",
]
MAX_LEN = {
    "slug": 64,
    "description": 100, "dept_slugs": 2000, "document_type_slugs": 2000, "keywords": 2000,
    "inclusion_context": 2000, "exclusion_context": 2000, "adjudication_hints_keep": 2000,
    "adjudication_hints_drop": 2000, "extra_guidance_urls": 10000, "extra_law_urls": 10000,
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG_RE = re.compile(r"^[a-z0-9_-]+$")


def prettify(slug: Optional[str]) -> str:
    """Turn a slug into a human display name, e.g. animal_liquid_waste or
    farm-slurry-storage -> 'Animal Liquid Waste' / 'Farm Slurry Storage'."""
    return (slug or "").replace("_", " ").replace("-", " ").strip().title()


_LABELS = {
    "slug": "Name for this category",
    "owner_email": "Owner Email", "description": "Description", "dept_slugs": "Departments",
    "include_child_orgs": "Include child organisations",
    "document_type_slugs": "Document Types", "keywords": "Keyword Search",
    "inclusion_context": "Include description", "exclusion_context": "Exclude description",
    "adjudication_hints_keep": "Keep Keywords", "adjudication_hints_drop": "Drop Keywords",
    "extra_guidance_urls": "Include Guidance URLs",
    "only_use_extra_guidance_urls": "Only Use Extra Guidance URLs",
    "extra_law_urls": "Include LAW URLs", "only_use_extra_law_urls": "Only Use Extra LAW URLs",
}


def label(field: str) -> str:
    return _LABELS.get(field, field)


def now_iso() -> str:
    return db.now_iso()


def validate(data: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable validation errors ([] means valid)."""
    errors: List[str] = []
    for f in REQUIRED_FIELDS:
        if not str(data.get(f) or "").strip():
            errors.append(f"{label(f)} is required.")
    email = str(data.get("owner_email") or "").strip()
    if email and not _EMAIL_RE.match(email):
        errors.append("Please enter a valid email address.")
    slug = str(data.get("slug") or "").strip()
    if "slug" in data and not slug:
        errors.append("Name for this category is required.")
    elif slug and not SLUG_RE.match(slug):
        errors.append("Name must be lowercase letters, numbers, hyphens and underscores only.")
    for f, limit in MAX_LEN.items():
        v = data.get(f)
        if v and len(str(v)) > limit:
            errors.append(f"{label(f)} must be {limit} characters or less.")
    # keywords: one rule per line, no line more than 2 words
    kw = str(data.get("keywords") or "").strip()
    for line in kw.splitlines():
        if len(line.split()) > 2:
            errors.append(f"Keyword line '{line.strip()}' has more than 2 words.")
            break
    return errors


def _coerce(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    for b in ("only_use_extra_guidance_urls", "only_use_extra_law_urls", "include_child_orgs"):
        out[b] = 1 if str(data.get(b) or "0") in ("1", "Y", "y", "True", "true", "on") else 0
    return out


def create_category(conn, data: Dict[str, Any], status: str = "draft") -> int:
    d = _coerce(data)
    cid = int(time.time() * 1000)
    ts = now_iso()
    cols = ["id", "created_at", "updated_at", "status"] + USER_FIELDS
    vals = [cid, ts, ts, status] + [d.get(f) for f in USER_FIELDS]
    ph = ",".join([_P] * len(cols))
    conn.execute(f"INSERT INTO categories ({','.join(cols)}) VALUES ({ph})", tuple(vals))
    conn.commit()
    return cid


def update_category(conn, cid: int, data: Dict[str, Any], status: Optional[str] = None) -> None:
    d = _coerce(data)
    sets = {**{f: d.get(f) for f in USER_FIELDS}, "updated_at": now_iso()}
    if status is not None:
        sets["status"] = status
    assignments = ",".join(f"{k}={_P}" for k in sets)
    conn.execute(f"UPDATE categories SET {assignments} WHERE id={_P}",
                 tuple(sets.values()) + (cid,))
    conn.commit()


def delete_category(conn, cid: int) -> None:
    conn.execute(f"DELETE FROM categories WHERE id={_P}", (cid,))
    conn.commit()


def get_category(conn, cid: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(f"SELECT * FROM categories WHERE id={_P}", (cid,)).fetchone()
    return dict(row) if row else None


def list_categories(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, slug, description, owner_email, dept_slugs, document_type_slugs, "
        "status, created_at, updated_at "
        "FROM categories ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def parse_list(value: Optional[str]) -> List[str]:
    """Split a comma/newline-separated field into clean tokens (for filters)."""
    if not value:
        return []
    parts = re.split(r"[,\n]", value)
    return [p.strip() for p in parts if p.strip()]
