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

from typing import Callable, List, Optional, Sequence

from . import categories as cat
from .backend import db
from .canonical import canonicalise

_P = "%s" if db.__name__.endswith("db_pg") else "?"

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
            search_fn: Callable[[Sequence[str], Sequence[str]], dict]) -> dict:
    """Run the org-scoped GOV.UK Search for `keywords`, compare with the category's stored
    deterministic shortlist, and persist the tagged union. `search_fn(phrases, orgs)` must
    return {"results": [{link, title, document_type, phrases}]}. Returns a summary dict."""
    keywords = [k for k in (keywords or []) if k]
    results = []
    if keywords:
        data = search_fn(keywords, list(organisations or []))
        results = (data or {}).get("results", []) or []

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
    return {"counts": counts, "total": total,
            "computed_at": (dict(computed_at)["t"] if computed_at else None)}


def has_comparison(conn, cid: int) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM category_search_pages WHERE category_id = {_P} LIMIT 1", (cid,)).fetchone()
    return row is not None


def augmented_pages(conn, cid: int, source: str = "", limit: int = 100, offset: int = 0) -> dict:
    """A page of the augmented shortlist, optionally filtered to one source."""
    where = [f"category_id = {_P}"]
    params: list = [cid]
    if source in ("shortlister", "both", "search"):
        where.append(f"source = {_P}")
        params.append(source)
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


def export_rows(conn, cid: int) -> List[dict]:
    """All augmented rows for a CSV export."""
    return [dict(r) for r in conn.execute(
        f"SELECT source, url, content_id, title, document_type, phrases "
        f"FROM category_search_pages WHERE category_id = {_P} "
        f"ORDER BY CASE source WHEN 'search' THEN 0 WHEN 'both' THEN 1 ELSE 2 END, url",
        (cid,)).fetchall()]
