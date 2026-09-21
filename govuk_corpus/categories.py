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
    "extra_law_urls", "only_use_extra_law_urls", "hybrid_on_save",
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


def slugify(name: Optional[str]) -> str:
    """A filename-safe slug derived from a free-text name (lowercase, alphanumerics -> hyphens).
    The category is identified by its `id` everywhere; this slug is only for export filenames and
    copy-naming, so it needn't be unique or stable."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:64]


def display_name(category: Dict[str, Any]) -> str:
    """The human name to show: the free-text name (stored in `description`), falling back to a
    prettified slug for older records, then 'Untitled'."""
    return (category.get("description") or "").strip() or prettify(category.get("slug")) or "Untitled"


_LABELS = {
    "slug": "Name for this category",
    "owner_email": "Owner Email", "description": "Name", "dept_slugs": "Departments",
    "include_child_orgs": "Include child organisations",
    "document_type_slugs": "Document Types", "keywords": "Keyword Search",
    "inclusion_context": "Include description", "exclusion_context": "Exclude description",
    "adjudication_hints_keep": "Keep Keywords", "adjudication_hints_drop": "Drop Keywords",
    "extra_guidance_urls": "Include Guidance URLs",
    "only_use_extra_guidance_urls": "Only Use Extra Guidance URLs",
    "extra_law_urls": "Include LAW URLs", "only_use_extra_law_urls": "Only Use Extra LAW URLs",
    "hybrid_on_save": "Run GOV.UK hybrid search on save",
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
    # Keywords: one term per line or comma-separated. Multi-word terms match as an adjacent
    # phrase (phraseto_tsquery), so terms of any length are allowed — no word-count limit.
    return errors


def _coerce(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    for b in ("only_use_extra_guidance_urls", "only_use_extra_law_urls", "include_child_orgs",
              "hybrid_on_save"):
        out[b] = 1 if str(data.get(b) or "0") in ("1", "Y", "y", "True", "true", "on") else 0
    return out


def create_category(conn, data: Dict[str, Any], status: str = "draft") -> int:
    d = _coerce(data)
    cid = int(time.time() * 1000)
    # Guard against PK collisions when two categories are created in the same
    # millisecond (e.g. copying a category twice in quick succession).
    while conn.execute(f"SELECT 1 FROM categories WHERE id={_P}", (cid,)).fetchone():
        cid += 1
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


def set_url_checklist(conn, cid: int, should_include_urls: str, should_exclude_urls: str) -> None:
    """Persist the URL-check lists (guc-0018) for a category. These are managed only on the
    URL check page — the category form doesn't carry them — so this updates just these two
    columns and leaves every other field untouched."""
    conn.execute(
        f"UPDATE categories SET should_include_urls={_P}, should_exclude_urls={_P}, "
        f"updated_at={_P} WHERE id={_P}",
        ((should_include_urls or "").strip() or None,
         (should_exclude_urls or "").strip() or None, now_iso(), cid))
    conn.commit()


def delete_category(conn, cid: int) -> None:
    """Delete a shortlist and everything scoped to it — its AI runs and per-page results,
    the audit rows, the cached page counts, and the stored shortlist / GOV.UK-search
    membership — then the category itself. One transaction; irreversible."""
    for tbl in ("evaluation_results", "evaluation_runs", "category_audit",
                "category_page_counts", "category_shortlist_pages", "category_search_pages"):
        conn.execute(f"DELETE FROM {tbl} WHERE category_id={_P}", (cid,))
    conn.execute(f"DELETE FROM categories WHERE id={_P}", (cid,))
    conn.commit()


def _copy_slug(conn, base: str) -> str:
    """A distinct slug for a duplicate: `<base>-copy`, then `-copy-2`, `-copy-3`…
    Category slugs aren't DB-unique, but keeping copies distinct keeps the list readable."""
    base = (base or "category").strip()[:56]  # leave room for the "-copy-N" suffix (≤64)
    rows = conn.execute("SELECT slug FROM categories").fetchall()
    taken = {(dict(r).get("slug") or "").strip() for r in rows}
    cand = f"{base}-copy"
    if cand not in taken:
        return cand
    i = 2
    while f"{base}-copy-{i}" in taken:
        i += 1
    return f"{base}-copy-{i}"


def copy_category(conn, cid: int, owner_email: Optional[str] = None) -> Optional[int]:
    """Duplicate a category's rules into a new draft. Returns the new id, or None if
    the source is missing. The copy gets a fresh, distinct slug (`<slug>-copy`), starts
    as a draft, and carries over every rule field — including the URL-check lists, which
    live outside USER_FIELDS. Pass owner_email to reassign the copy to the current user."""
    src = get_category(conn, cid)
    if not src:
        return None
    data = {f: src.get(f) for f in USER_FIELDS}
    data["slug"] = _copy_slug(conn, src.get("slug") or "")
    if owner_email:
        data["owner_email"] = owner_email
    new_id = create_category(conn, data, status="draft")
    inc = (src.get("should_include_urls") or "").strip()
    exc = (src.get("should_exclude_urls") or "").strip()
    if inc or exc:
        set_url_checklist(conn, new_id, inc, exc)
    return new_id


def get_category(conn, cid: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(f"SELECT * FROM categories WHERE id={_P}", (cid,)).fetchone()
    return dict(row) if row else None


def list_categories_query():
    """(sql, params) for the categories list — so the page can show the SQL it ran."""
    return ("SELECT id, slug, description, owner_email, dept_slugs, document_type_slugs, "
            "status, created_at, updated_at "
            "FROM categories ORDER BY created_at DESC", [])


def list_categories(conn) -> List[Dict[str, Any]]:
    sql, params = list_categories_query()
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def parse_list(value: Optional[str]) -> List[str]:
    """Split a comma/newline-separated field into clean tokens (for filters)."""
    if not value:
        return []
    parts = re.split(r"[,\n]", value)
    return [p.strip() for p in parts if p.strip()]
