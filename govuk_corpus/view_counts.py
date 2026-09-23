"""Collect GOV.UK Search `view_count` for corpus pages and store it on `content`.

GOV.UK's Search API exposes a per-page `view_count` (≈ pageviews over the last ~14 days —
"vc_14" in the page-traffic index, refreshed nightly). After a shortlist is (re)built we look
up each shortlisted page via the Search API (batched, repeatable `filter_link`) and upsert
`content.view_count` plus `content.view_count_updated` (the date we collected it).

CLI:
    python3 -m govuk_corpus.view_counts --category 1789943869717   # one shortlist
    python3 -m govuk_corpus.view_counts --all                      # every shortlist's pages
    python3 -m govuk_corpus.view_counts --all --force              # ignore "already collected today"
"""
from __future__ import annotations

import argparse
from datetime import date
from typing import Dict, Iterable, Optional
from urllib.parse import urlparse

import httpx

from .backend import db

# Active SQL parameter placeholder for the selected backend (Postgres %s / SQLite ?).
_P = "%s" if db.__name__.endswith("db_pg") else "?"

SEARCH_URL = "https://www.gov.uk/api/search.json"
_UA = {"User-Agent": "gov-uk-corpus-shortlist-builder", "Accept": "application/json"}
_BATCH = 25           # GOV.UK silently honours only ~30 repeated `filter_link` values per
                      # request (a longer query drops most matches), so keep batches small
_TIMEOUT = 20.0


def _link_of(url: str) -> str:
    """The GOV.UK Search `link` (site-relative path) for a corpus url."""
    if not url:
        return ""
    return urlparse(url).path if url.startswith("http") else url


def _parent_link(link: str) -> Optional[str]:
    """Parent publication/guide link for an attachment or guide-part sub-page, or None.
    `/government/publications/PARENT/attachment` -> `/government/publications/PARENT`;
    `/guidance/GUIDE/part` -> `/guidance/GUIDE`. Requires at least 3 path segments so we never
    strip a top-level page down to a finder root (`/guidance`, `/government/publications`); those
    aren't indexed anyway, so an over-eager strip just returns no match (self-correcting)."""
    segs = [s for s in (link or "").split("/") if s]
    if len(segs) < 3:
        return None
    return "/" + "/".join(segs[:-1])


def fetch_view_counts(links: Iterable[str], *, batch: int = _BATCH,
                      timeout: float = _TIMEOUT) -> Dict[str, int]:
    """{link: view_count} for the links GOV.UK Search returns. Best-effort: a failed batch is
    skipped (its pages simply get no value this run), never raised."""
    uniq = [l for l in dict.fromkeys(links) if l]
    out: Dict[str, int] = {}
    for i in range(0, len(uniq), batch):
        chunk = uniq[i:i + batch]
        params = [("count", str(len(chunk))), ("fields", "view_count"), ("fields", "link")]
        params += [("filter_link", l) for l in chunk]
        try:
            resp = httpx.get(SEARCH_URL, params=params, headers=_UA, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue
        for item in data.get("results", []):
            link, vc = item.get("link"), item.get("view_count")
            if link and vc is not None:
                try:
                    out[link] = int(vc)
                except (TypeError, ValueError):
                    pass
    return out


def collect_for_urls(conn, urls: Iterable[str], *, when: Optional[str] = None,
                     parent_fallback: bool = True) -> Dict[str, object]:
    """Look up view_count for `urls` and upsert content.view_count + view_count_updated (the
    date collected). Matched back from GOV.UK's `link` to the url we were given.

    Attachment / guide-part sub-pages aren't separate documents in GOV.UK Search, so with
    `parent_fallback` (default) a page GOV.UK doesn't index directly inherits its parent
    publication's view_count (the closest available popularity signal)."""
    when = when or date.today().isoformat()
    link_to_url: Dict[str, str] = {}
    for u in urls:
        link = _link_of(u)
        if link:
            link_to_url.setdefault(link, u)

    direct = fetch_view_counts(link_to_url.keys())          # pass 1: the page's own count
    resolved: Dict[str, int] = {u: direct[l] for l, u in link_to_url.items() if l in direct}

    inherited = 0
    if parent_fallback:
        # pass 2: for pages GOV.UK didn't index, look up the parent publication's count.
        parents: Dict[str, list] = {}
        for link, u in link_to_url.items():
            if u in resolved:
                continue
            p = _parent_link(link)
            if p:
                parents.setdefault(p, []).append(u)
        if parents:
            pcounts = fetch_view_counts(parents.keys())
            for p, kids in parents.items():
                if p in pcounts:
                    for u in kids:
                        resolved[u] = pcounts[p]
                        inherited += 1

    for u, vc in resolved.items():
        conn.execute(
            f"UPDATE content SET view_count = {_P}, view_count_updated = {_P} WHERE url = {_P}",
            (vc, when, u))
    conn.commit()
    return {"requested": len(link_to_url), "direct": len(direct), "inherited": inherited,
            "updated": len(resolved), "date": when}


def collect_for_category(conn, cid: int, *, skip_if_collected_today: bool = True) -> Dict[str, object]:
    """Collect view_count for a category's materialised shortlist (`category_shortlist_pages`).
    By default pages already collected today are skipped, so re-saving a shortlist is cheap."""
    when = date.today().isoformat()
    if skip_if_collected_today:
        rows = conn.execute(
            f"SELECT sp.url AS url FROM category_shortlist_pages sp "
            f"LEFT JOIN content c ON c.url = sp.url "
            f"WHERE sp.category_id = {_P} "
            f"AND (c.view_count_updated IS NULL OR c.view_count_updated <> {_P})",
            (cid, when)).fetchall()
    else:
        rows = conn.execute(
            f"SELECT url FROM category_shortlist_pages WHERE category_id = {_P}", (cid,)).fetchall()
    urls = [dict(r).get("url") for r in rows]
    return collect_for_urls(conn, [u for u in urls if u], when=when)


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect GOV.UK Search view_count into content.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    ap.add_argument("--category", type=int, help="Collect for one category's shortlist")
    ap.add_argument("--all", action="store_true", help="Collect for every category's shortlist")
    ap.add_argument("--force", action="store_true", help="Re-collect even if collected today")
    args = ap.parse_args()
    conn = db.connect(args.db)
    if args.category:
        print(f"[view_count] category {args.category}: "
              f"{collect_for_category(conn, args.category, skip_if_collected_today=not args.force)}")
    elif args.all:
        ids = [int(dict(r)["category_id"]) for r in conn.execute(
            "SELECT DISTINCT category_id FROM category_shortlist_pages").fetchall()]
        for cid in ids:
            print(f"[view_count] category {cid}: "
                  f"{collect_for_category(conn, cid, skip_if_collected_today=not args.force)}")
    else:
        ap.error("pass --category <id> or --all")
    conn.close()


if __name__ == "__main__":
    main()
