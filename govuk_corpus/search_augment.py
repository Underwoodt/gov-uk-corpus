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


def _load_list(v) -> list:
    """Parse a stored JSON array (matched keywords); tolerate NULL / bad data."""
    if not v:
        return []
    try:
        out = json.loads(v)
        return out if isinstance(out, list) else []
    except Exception:
        return []


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
    """Replace a category's augmented rows. Each row:
    (url, content_id, title, doctype, source, phrases, corpus_phrases, es_score) where phrases
    is the GOV.UK-Search matched keywords (comma-joined), corpus_phrases is a JSON array of the
    keywords the page matched in our corpus, and es_score is GOV.UK's relevance score."""
    ts = cat.now_iso()
    conn.execute(f"DELETE FROM category_search_pages WHERE category_id = {_P}", (cid,))
    CHUNK = 400
    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i + CHUNK]
        values = ",".join([f"({_P}, {_P}, {_P}, {_P}, {_P}, {_P}, {_P}, {_P}, {_P}, {_P})"] * len(batch))
        params: list = []
        for url, content_id, title, doctype, source, phrases, corpus_phrases, es_score in batch:
            params.extend((cid, url, content_id, title, doctype, source, phrases, corpus_phrases, es_score, ts))
        conn.execute(
            "INSERT INTO category_search_pages "
            "(category_id, url, content_id, title, document_type, source, phrases, corpus_phrases, "
            "es_score, computed_at) "
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
                           "phrases": it.get("phrases") or [], "es_score": it.get("es_score")}

    membership = [dict(r) for r in conn.execute(
        f"SELECT content_id, url, matched_keywords FROM category_shortlist_pages WHERE category_id = {_P}",
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
        in_search = m["content_id"] in search_keys
        source = "both" if in_search else "shortlister"
        # GOV.UK phrases + relevance: only for 'both' pages (which GOV.UK Search also returned).
        meta = search_meta[search_keys[m["content_id"]]] if in_search else None
        govuk = ", ".join(meta["phrases"]) if meta else None
        es = meta.get("es_score") if meta else None
        # Corpus phrases: the keywords this page matched in our corpus, carried from membership.
        rows.append((url, m["content_id"], title, eff, source, govuk, m.get("matched_keywords"), es))

    for k, cu in search_keys.items():
        if k in shortlist_keys:
            continue                                   # already emitted as 'both'
        _, cidv = key_for(cu)
        meta = search_meta[cu]
        # search-only: no corpus match (it wasn't in our deterministic shortlist).
        rows.append((cu, cidv, meta["title"], meta["doctype"], "search",
                     ", ".join(meta["phrases"]), None, meta.get("es_score")))

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


_JOIN = "category_search_pages sp LEFT JOIN content c ON c.url = sp.url"


def _coerce_score(min_score) -> float:
    try:
        return max(0.0, float(min_score or 0))
    except (TypeError, ValueError):
        return 0.0


def _where_clause(cid: int, source: str = "", q: str = "", loaded: str = "",
                  min_score: float = 0.0):
    """(where_sql, params) shared by the list and the summary, over `_JOIN`. `loaded`:
    'in' = present in corpus, 'missing' = not; `min_score`: drop rows WITH an es_score below
    it (rows with no score are kept)."""
    where = [f"sp.category_id = {_P}"]
    params: list = [cid]
    if source in ("shortlister", "both", "search"):
        where.append(f"sp.source = {_P}")
        params.append(source)
    q = (q or "").strip()
    if q:
        where.append(f"LOWER(sp.title) LIKE LOWER({_P})")
        params.append("%" + q + "%")
    if loaded == "in":
        where.append("c.url IS NOT NULL")
    elif loaded == "missing":
        where.append("c.url IS NULL")
    ms = _coerce_score(min_score)
    if ms > 0:
        # The relevance threshold only weeds GOV.UK-Search-ONLY rows (the false-positive
        # candidates). 'shortlister'/'both' pages are in our deterministic shortlist because our
        # own filters matched the keywords, so a low GOV.UK es_score must NOT drop them —
        # otherwise shortlister+both would stop equalling the shortlist size.
        where.append(f"(sp.source <> 'search' OR sp.es_score IS NULL OR sp.es_score >= {_P})")
        params.append(ms)
    return " AND ".join(where), params


def filtered_summary(conn, cid: int, q: str = "", min_score: float = 0.0) -> dict:
    """Per-source counts (+ evaluable / pending) over the SAME narrowing thresholds as the
    list — the minimum GOV.UK relevance and the title search — so the sums at the top of the
    page track the threshold. Source and corpus-status view filters are NOT applied (the sums
    stay a full per-source breakdown). Recomputed on every list load."""
    counts = {"shortlister": 0, "both": 0, "search": 0}
    wsql, params = _where_clause(cid, "", q, "", min_score)
    for r in conn.execute(
            f"SELECT sp.source AS s, COUNT(*) AS n FROM {_JOIN} WHERE {wsql} GROUP BY sp.source",
            tuple(params)).fetchall():
        d = dict(r)
        if d["s"] in counts:
            counts[d["s"]] = d["n"]
    ew, ep = _where_clause(cid, "search", q, "in", min_score)       # GOV.UK-only, in corpus
    evaluable = conn.execute(f"SELECT COUNT(*) AS n FROM {_JOIN} WHERE {ew}", tuple(ep)).fetchone()["n"]
    pw, pp = _where_clause(cid, "search", q, "missing", min_score)  # GOV.UK-only, not fetched
    pending = conn.execute(f"SELECT COUNT(*) AS n FROM {_JOIN} WHERE {pw}", tuple(pp)).fetchone()["n"]
    return {"counts": counts, "total": sum(counts.values()), "evaluable": evaluable,
            "pending_fetch": pending}


def augmented_pages(conn, cid: int, source: str = "", limit: int = 100, offset: int = 0,
                    q: str = "", loaded: str = "", min_score: float = 0.0) -> dict:
    """A page of the augmented shortlist, optionally filtered to one source, a title substring
    `q` (case-insensitive, over the WHOLE stored set), corpus status `loaded`
    ('in' = in the corpus, 'missing' = not fetched yet), and/or a minimum GOV.UK relevance
    `min_score` (drops GOV.UK-Search-ONLY rows with an es_score below it; shortlister/both are
    never dropped by it)."""
    wsql, params = _where_clause(cid, source, q, loaded, min_score)
    join = _JOIN
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM {join} WHERE {wsql}", tuple(params)).fetchone()["n"]
    # Order: search-only first (the gaps), then both, then shortlister; url within.
    order = ("CASE sp.source WHEN 'search' THEN 0 WHEN 'both' THEN 1 ELSE 2 END, sp.url"
             if not source else "sp.url")
    # `loaded` = the url is present in our corpus (fetched). LEFT JOIN so search-only pages we
    # haven't fetched come back as not loaded.
    rows = [dict(r) for r in conn.execute(
        f"SELECT sp.url AS url, sp.content_id AS content_id, sp.title AS title, "
        f"sp.document_type AS document_type, sp.source AS source, sp.phrases AS phrases, "
        f"sp.corpus_phrases AS corpus_phrases, sp.es_score AS es_score, "
        f"CASE WHEN c.url IS NOT NULL THEN 1 ELSE 0 END AS loaded "
        f"FROM {join} "
        f"WHERE {wsql} ORDER BY {order} LIMIT {_P} OFFSET {_P}",
        tuple(params) + (limit, offset)).fetchall()]
    for r in rows:
        # GOV.UK matched keywords (comma-joined) and corpus matched keywords (JSON) -> arrays.
        r["govuk_keywords"] = [p.strip() for p in (r.get("phrases") or "").split(",") if p.strip()]
        r["corpus_keywords"] = _load_list(r.get("corpus_phrases"))
        r["loaded"] = bool(r.get("loaded"))
    return {"total": total, "rows": rows, "limit": limit, "offset": offset, "source": source}


def resolve_attachment_containers(conn, cid: int) -> dict:
    """Drop GOV.UK-Search-only *container* rows whose HTML-publication attachment(s) we already
    hold, so they're not counted as coverage gaps for content we have under a different URL.

    GOV.UK Search returns publication container pages (often little content of their own) whose
    actual readable content is an ``html_publication`` attachment — and that attachment is
    usually already in our corpus. For each still-``search`` row we now have a payload for, we
    read its HTML attachments (``details.attachments`` html; url + ``https://www.gov.uk``). If
    EVERY html attachment is already in the corpus, we drop the container row and re-tag each
    attachment: ``both`` when it's in this category's deterministic shortlist, else ``search``
    (GOV.UK-only). If any html attachment isn't in the corpus, the container is left alone.
    Returns {containers_dropped, attachments_both, attachments_search}."""
    from .extract import html_attachment_urls
    shortlist_ids = {dict(r)["content_id"] for r in conn.execute(
        f"SELECT content_id FROM category_shortlist_pages WHERE category_id = {_P}", (cid,)).fetchall()}
    rows = conn.execute(
        f"SELECT sp.url AS url, sp.phrases AS phrases, sp.es_score AS es_score, c.content AS content "
        f"FROM category_search_pages sp JOIN content c ON c.url = sp.url "
        f"WHERE sp.category_id = {_P} AND sp.source = 'search' AND c.content IS NOT NULL",
        (cid,)).fetchall()
    dropped = both = searched = 0
    for r in rows:
        d = dict(r)
        try:
            payload = json.loads(d["content"]) if isinstance(d["content"], str) else d["content"]
        except Exception:
            continue
        atts = html_attachment_urls(payload or {})
        if not atts:
            continue
        lookup = _content_lookup(conn, atts)
        if not all(a in lookup for a in atts):
            continue                              # some attachment not in corpus -> leave alone
        # GOV.UK found the CONTAINER for these phrases; its content is the attachment, so the
        # attachment inherits the container's GOV.UK terms.
        govuk_phrases = d.get("phrases")
        govuk_es = d.get("es_score")
        for a in atts:
            cidv, title, eff = lookup[a]
            if (cidv or a) in shortlist_ids:      # in the deterministic shortlist -> upgrade to 'both'
                conn.execute(
                    f"UPDATE category_search_pages SET source = 'both', "
                    f"phrases = COALESCE(NULLIF(phrases, ''), {_P}), es_score = COALESCE(es_score, {_P}) "
                    f"WHERE category_id = {_P} AND content_id = {_P} AND source = 'shortlister'",
                    (govuk_phrases, govuk_es, cid, cidv))
                both += 1
            else:                                 # in the corpus but not the shortlist -> GOV.UK-only row
                exists = conn.execute(
                    f"SELECT 1 FROM category_search_pages WHERE category_id = {_P} AND url = {_P}",
                    (cid, a)).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO category_search_pages (category_id, url, content_id, title, "
                        "document_type, source, phrases, corpus_phrases, es_score, computed_at) "
                        f"VALUES ({_P}, {_P}, {_P}, {_P}, {_P}, 'search', {_P}, NULL, {_P}, {_P})",
                        (cid, a, cidv, title, eff, govuk_phrases, govuk_es, cat.now_iso()))
                    searched += 1
        conn.execute(f"DELETE FROM category_search_pages WHERE category_id = {_P} AND url = {_P}",
                     (cid, d["url"]))
        dropped += 1
    conn.commit()
    return {"containers_dropped": dropped, "attachments_both": both, "attachments_search": searched}


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
    """All augmented rows for a CSV export, with the corpus and GOV.UK keyword hits."""
    out = []
    for r in conn.execute(
            f"SELECT source, url, content_id, title, document_type, phrases, corpus_phrases, es_score "
            f"FROM category_search_pages WHERE category_id = {_P} "
            f"ORDER BY CASE source WHEN 'search' THEN 0 WHEN 'both' THEN 1 ELSE 2 END, url",
            (cid,)).fetchall():
        d = dict(r)
        d["govuk_keywords"] = d.pop("phrases", None) or ""            # comma-joined already
        d["corpus_keywords"] = ", ".join(_load_list(d.pop("corpus_phrases", None)))
        out.append(d)
    return out
