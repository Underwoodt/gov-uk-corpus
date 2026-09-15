"""Find gov.uk links referenced by a category's pages that are NOT in the corpus.

Scans the body HTML of the shortlisted pages, pulls out every fully-qualified
`https://www.gov.uk/...` <a href> (site-relative and other links are ignored),
canonicalises it to a page URL, and reports the targets that don't exist in `content`.

Reading page bodies (the TOASTed content JSON) is expensive, so the scan is capped at
`sample_limit` pages and flags when the cap bit.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Dict, List, Optional

from .backend import db
from .canonical import canonicalise
from . import shortlist

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

# href="..." / href='...' up to the first quote, '#', space or '>'.
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\'#>\s]+)', re.IGNORECASE)


def _body_html(payload: dict) -> str:
    details = payload.get("details") or {}
    pieces = [details.get("body") or ""]
    for part in details.get("parts") or []:
        pieces.append(part.get("body") or "")
    return " ".join(p for p in pieces if isinstance(p, str))


def extract_govuk_links(content_json: Optional[str]) -> List[str]:
    """Canonical gov.uk page URLs referenced by <a href> in a page's body.

    Only fully-qualified `https://www.gov.uk/...` hrefs are considered — the same
    form the corpus stores, so the lookup is like-for-like with no host guessing.
    Everything else (site-relative `/...`, `http://`, protocol-relative, other
    hosts, mailto:, anchors) is ignored.
    """
    if not content_json:
        return []
    try:
        payload = json.loads(content_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    out = []
    for href in _HREF_RE.findall(_body_html(payload)):
        low = href.strip().lower()
        if not (low == "https://www.gov.uk" or low.startswith("https://www.gov.uk/")):
            continue                                        # absolute https://www.gov.uk links only
        cu = canonicalise(href.strip())                     # strips query/fragment/trailing slash
        if cu:
            out.append(cu)
    return out


def sql_text(*, organisations=(), document_types=(), keywords=(),
             match: str = "any", sample_limit: int = 500) -> str:
    """Readable SQL behind the two-step scan, for the on-page 'SQL' box.

    Finding missing links isn't a single query: step 1 pulls the shortlist pages'
    bodies, step 2 checks which referenced gov.uk URLs already exist in `content`
    (the href parsing / canonicalising in between happens in Python)."""
    scan_sql, scan_params = shortlist.build_query(
        select_expr="c.url AS url, c.content AS content", limit=sample_limit,
        organisations=organisations, document_types=document_types,
        keywords=keywords, match=match)
    scan = shortlist.interpolate_sql(scan_sql, scan_params)
    check = ("SELECT url FROM content\n"
             "WHERE url IN (:link_1, :link_2, ...)   -- the gov.uk URLs found in the bodies\n"
             "-- (run in chunks; any referenced URL NOT returned here is a missing link)")
    return (f"-- Step 1: read the body of each shortlisted page (capped at {sample_limit})\n"
            f"{shortlist.pretty_sql(scan)}\n\n"
            "-- Step 2: of the gov.uk links parsed from those bodies, which already exist?\n"
            f"{check}")


def find_missing_links(conn, *, organisations=(), document_types=(), keywords=(),
                       match: str = "any", sample_limit: int = 500,
                       max_pairs: int = 2000) -> Dict:
    """Per (source page, missing link) pairs — each shortlist page and the gov.uk links
    in its body that point to pages not in the corpus."""
    _keys, rows = shortlist.export_rows(
        conn, ["url", "content"], limit=sample_limit + 1,
        organisations=organisations, document_types=document_types,
        keywords=keywords, match=match)
    sampled = len(rows) > sample_limit
    rows = rows[:sample_limit]

    page_targets = []       # (source_url, {referenced target urls})
    all_targets = set()
    for r in rows:
        targets = {t for t in extract_govuk_links(r["content"]) if t != r["url"]}
        if targets:
            page_targets.append((r["url"], targets))
            all_targets |= targets

    # Which referenced targets already exist in the corpus? (chunked IN lookups).
    existing = set()
    tlist = list(all_targets)
    for i in range(0, len(tlist), 900):
        chunk = tlist[i:i + 900]
        ph = ",".join([_P] * len(chunk))
        for row in conn.execute(f"SELECT url FROM content WHERE url IN ({ph})", tuple(chunk)).fetchall():
            existing.add(row["url"])

    pairs = []
    missing_targets = set()
    pages_with_missing = set()
    pair_count = 0
    for src, targets in page_targets:
        for t in sorted(targets):
            if t not in existing:
                pair_count += 1
                missing_targets.add(t)
                pages_with_missing.add(src)
                if len(pairs) < max_pairs:
                    pairs.append({"page": src, "link": t})
    return {
        "pairs": pairs,
        "pair_count": pair_count,
        "pages_with_missing": len(pages_with_missing),
        "missing_count": len(missing_targets),
        "distinct_links": len(all_targets),
        "scanned": len(rows),
        "sampled": sampled,
        "truncated": pair_count > len(pairs),
    }
