"""GOV.UK Search coverage: compare a category's deterministic shortlist against an
org-scoped GOV.UK Search of the same keywords, tag every page by provenance, and persist
the augmented shortlist in ``category_search_pages``.

source values:
  * ``shortlister`` — only our organisation+document-type+keyword filters found it,
  * ``both``        — found by both our filters and GOV.UK Search,
  * ``search``      — GOV.UK Search only (may not be in our corpus, so content_id is NULL).

Pages are matched on the same dedup key the membership uses — ``COALESCE(content_id, url)``
— so a page reached via a different url alias still matches. Backend-agnostic; the caller
supplies the search function (so the module needn't know about the web layer).
"""
from __future__ import annotations

import json
from typing import Callable, List, Optional, Sequence

from . import categories as cat
from . import settings
from .backend import db
from .canonical import canonicalise

_P = "%s" if db.__name__.endswith("db_pg") else "?"


def _qkey(cid: int) -> str:
    return f"govuk_queries_{cid}"


def _tkey(cid: int) -> str:
    return f"govuk_thresholds_{cid}"

# Effective document type without the JSON dig (parent_document_type is backfilled).
_EFF = ("CASE WHEN c.document_type = 'html_publication' "
        "THEN COALESCE(NULLIF(c.parent_document_type, ''), c.document_type) "
        "ELSE c.document_type END")


def _content_lookup(conn, urls: Sequence[str]) -> dict:
    """{url: (content_id, title, effective_document_type)} for those urls present in content."""
    out: dict = {}
    urls = [u for u in dict.fromkeys(u for u in urls if u)]   # de-dup, keep order
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        ph = ",".join([_P] * len(chunk))
        for r in conn.execute(
                f"SELECT url, content_id, title, {_EFF} AS eff FROM content c "
                f"WHERE url IN ({ph})", tuple(chunk)).fetchall():
            d = dict(r)
            out[d["url"]] = (d.get("content_id"), d.get("title"), d.get("eff"))
    return out


def _store(conn, cid: int, rows: List[tuple]) -> None:
    """Replace a category's augmented rows. Each row: (url, content_id, title, doctype, source, phrases)."""
    ts = cat.now_iso()
    conn.execute(f"DELETE FROM category_search_pages WHERE category_id = {_P}", (cid,))
    CHUNK = 400
    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i + CHUNK]
        values = ",".join([f"({_P}, {_P}, {_P}, {_P}, {_P}, {_P}, {_P}, {_P})"] * len(batch))
        params: list = []
        for url, content_id, title, doctype, source, phrases in batch:
            params.extend((cid, url, content_id, title, doctype, source, phrases, ts))
        conn.execute(
            "INSERT INTO category_search_pages "
            "(category_id, url, content_id, title, document_type, source, phrases, computed_at) "
            f"VALUES {values}", tuple(params))


def compare(conn, cid: int, keywords: Sequence[str], organisations: Sequence[str],
            search_fn: Callable[..., dict], document_types: Sequence[str] = (),
            progress=None) -> dict:
    """Run the org- and document-type-scoped GOV.UK Search for `keywords`, compare with the
    category's stored deterministic shortlist, and persist the tagged union.
    `search_fn(phrases, orgs, doctypes, progress)` must return {"results": [{link, title,
    document_type, phrases}]}. `progress(done, total, pages)` is an optional per-phrase
    callback. Returns a summary dict."""
    keywords = [k for k in (keywords or []) if k]
    results, query_urls, thresholds = [], [], {}
    if keywords:
        data = search_fn(keywords, list(organisations or []), list(document_types or []), progress) or {}
        results = data.get("results", []) or []
        query_urls = data.get("query_urls", []) or []
        thresholds = data.get("thresholds", {}) or {}
    # Persist the exact GOV.UK API queries used (expert box) and the threshold check.
    settings.set_setting(conn, _qkey(cid), json.dumps(query_urls))
    settings.set_setting(conn, _tkey(cid), json.dumps(thresholds))

    # Canonicalise search links; keep first occurrence of each canonical url.
    search_urls: List[str] = []
    search_meta: dict = {}
    for it in results:
        cu = canonicalise(it.get("link") or "") or (it.get("link") or "")
        if not cu or cu in search_meta:
            continue
        search_urls.append(cu)
        search_meta[cu] = {"title": (it.get("title") or cu), "doctype": (it.get("document_type") or ""),
                           "phrases": it.get("phrases") or []}

    membership = [dict(r) for r in conn.execute(
        f"SELECT content_id, url FROM category_shortlist_pages WHERE category_id = {_P}",
        (cid,)).fetchall()]

    lookup = _content_lookup(conn, [m["url"] for m in membership] + search_urls)

    # Dedup key = COALESCE(content_id, url), matching how membership was built.
    def key_for(url):
        cidv = lookup.get(url, (None, None, None))[0]
        return (cidv or url), cidv

    search_keys: dict = {}   # dedup key -> canonical url
    for cu in search_urls:
        k, _ = key_for(cu)
        search_keys.setdefault(k, cu)

    shortlist_keys = {m["content_id"] for m in membership}

    rows: List[tuple] = []
    for m in membership:
        url = m["url"]
        title = lookup.get(url, (None, None, None))[1]
        eff = lookup.get(url, (None, None, None))[2]
        source = "both" if m["content_id"] in search_keys else "shortlister"
        rows.append((url, m["content_id"], title, eff, source, None))

    for k, cu in search_keys.items():
        if k in shortlist_keys:
            continue                                   # already emitted as 'both'
        _, cidv = key_for(cu)
        meta = search_meta[cu]
        rows.append((cu, cidv, meta["title"], meta["doctype"], "search", ", ".join(meta["phrases"])))

    _store(conn, cid, rows)
    conn.commit()
    return summary(conn, cid)


# ---- read helpers --------------------------------------------------------

def summary(conn, cid: int) -> dict:
    counts = {"shortlister": 0, "both": 0, "search": 0}
    for r in conn.execute(
            f"SELECT source, COUNT(*) AS n FROM category_search_pages WHERE category_id = {_P} "
            f"GROUP BY source", (cid,)).fetchall():
        d = dict(r)
        counts[d["source"]] = d["n"]
    computed_at = conn.execute(
        f"SELECT MAX(computed_at) AS t FROM category_search_pages WHERE category_id = {_P}",
        (cid,)).fetchone()
    total = sum(counts.values())
    q = settings.get_setting(conn, _qkey(cid))
    t = settings.get_setting(conn, _tkey(cid))
    try:
        query_urls = json.loads(q) if q else []
    except Exception:
        query_urls = []
    try:
        thresholds = json.loads(t) if t else {}
    except Exception:
        thresholds = {}
    return {"counts": counts, "total": total, "query_urls": query_urls, "thresholds": thresholds,
            "computed_at": (dict(computed_at)["t"] if computed_at else None)}


def has_comparison(conn, cid: int) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM category_search_pages WHERE category_id = {_P} LIMIT 1", (cid,)).fetchone()
    return row is not None


def augmented_pages(conn, cid: int, source: str = "", limit: int = 100, offset: int = 0,
                    q: str = "") -> dict:
    """A page of the augmented shortlist, optionally filtered to one source and/or a title
    substring `q` (case-insensitive, over the WHOLE stored set — not just this page)."""
    where = [f"category_id = {_P}"]
    params: list = [cid]
    if source in ("shortlister", "both", "search"):
        where.append(f"source = {_P}")
        params.append(source)
    q = (q or "").strip()
    if q:
        where.append(f"LOWER(title) LIKE LOWER({_P})")
        params.append("%" + q + "%")
    wsql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM category_search_pages WHERE {wsql}", tuple(params)).fetchone()["n"]
    # Order: search-only first (the gaps), then both, then shortlister; url within.
    order = ("CASE source WHEN 'search' THEN 0 WHEN 'both' THEN 1 ELSE 2 END, url"
             if not source else "url")
    rows = [dict(r) for r in conn.execute(
        f"SELECT url, content_id, title, document_type, source, phrases "
        f"FROM category_search_pages WHERE {wsql} ORDER BY {order} LIMIT {_P} OFFSET {_P}",
        tuple(params) + (limit, offset)).fetchall()]
    return {"total": total, "rows": rows, "limit": limit, "offset": offset, "source": source}


def pending_fetch_urls(conn, cid: int) -> List[str]:
    """GOV.UK-Search-only URLs for this category that aren't in the corpus yet — the ones to
    fetch so they gain content/content_id/search_text and become LLM-evaluable."""
    rows = conn.execute(
        f"SELECT sp.url FROM category_search_pages sp "
        f"LEFT JOIN content c ON c.url = sp.url "
        f"WHERE sp.category_id = {_P} AND sp.source = 'search' AND c.url IS NULL",
        (cid,)).fetchall()
    return [dict(r)["url"] for r in rows]


def refresh_content_ids(conn, cid: int) -> int:
    """After fetching search-only pages, backfill content_id/title/document_type on the
    search rows now present in the corpus. Returns the number updated."""
    rows = conn.execute(
        f"SELECT sp.url AS url, c.content_id AS cid, c.title AS title, {_EFF} AS eff "
        f"FROM category_search_pages sp JOIN content c ON c.url = sp.url "
        f"WHERE sp.category_id = {_P} AND sp.source = 'search' "
        f"AND (sp.content_id IS NULL OR sp.content_id = '')",
        (cid,)).fetchall()
    n = 0
    for r in rows:
        d = dict(r)
        conn.execute(
            f"UPDATE category_search_pages SET content_id = {_P}, title = COALESCE({_P}, title), "
            f"document_type = COALESCE({_P}, document_type) WHERE category_id = {_P} AND url = {_P}",
            (d.get("cid"), d.get("title"), d.get("eff"), cid, d["url"]))
        n += 1
    conn.commit()
    return n


def evaluable_search_only(conn, cid: int) -> int:
    """How many GOV.UK-Search-only pages are already in the corpus (i.e. evaluable now)."""
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM category_search_pages sp JOIN content c ON c.url = sp.url "
        f"WHERE sp.category_id = {_P} AND sp.source = 'search' "
        f"AND c.is_redirect = 0 AND c.content_hash IS NOT NULL", (cid,)).fetchone()["n"]


def export_rows(conn, cid: int) -> List[dict]:
    """All augmented rows for a CSV export."""
    return [dict(r) for r in conn.execute(
        f"SELECT source, url, content_id, title, document_type, phrases "
        f"FROM category_search_pages WHERE category_id = {_P} "
        f"ORDER BY CASE source WHEN 'search' THEN 0 WHEN 'both' THEN 1 ELSE 2 END, url",
        (cid,)).fetchall()]
