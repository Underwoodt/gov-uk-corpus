"""Find gov.uk links referenced by a category's pages that are NOT in the corpus.

Scans the body HTML of the shortlisted pages, pulls out every gov.uk <a href>,
canonicalises it to a page URL, and reports the targets that don't exist in `content`
— ranked by how many shortlist pages link to each (the most-referenced gaps first).

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
    """Canonical gov.uk page URLs referenced by <a href> in a page's body."""
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
        href = href.strip()
        if href.startswith("/"):
            target = "https://www.gov.uk" + href           # site-relative
        elif href.lower().startswith(("http://", "https://")):
            target = href                                   # absolute (canonicalise filters non-gov.uk)
        else:
            continue                                        # mailto:, anchors, other schemes
        cu = canonicalise(target)
        if cu:
            out.append(cu)
    return out


def find_missing_links(conn, *, organisations=(), document_types=(), keywords=(),
                       match: str = "any", sample_limit: int = 500) -> Dict:
    _keys, rows = shortlist.export_rows(
        conn, ["url", "content"], limit=sample_limit + 1,
        organisations=organisations, document_types=document_types,
        keywords=keywords, match=match)
    sampled = len(rows) > sample_limit
    rows = rows[:sample_limit]

    tally: Counter = Counter()
    for r in rows:
        # Dedupe per source page: a target counts once per page that links to it.
        for target in set(extract_govuk_links(r["content"])):
            if target != r["url"]:
                tally[target] += 1

    # Which referenced targets already exist in the corpus? (chunked IN lookups).
    targets = list(tally)
    existing = set()
    for i in range(0, len(targets), 900):
        chunk = targets[i:i + 900]
        ph = ",".join([_P] * len(chunk))
        for row in conn.execute(f"SELECT url FROM content WHERE url IN ({ph})", tuple(chunk)).fetchall():
            existing.add(row["url"])

    missing = [{"url": u, "count": c} for u, c in tally.items() if u not in existing]
    missing.sort(key=lambda m: (-m["count"], m["url"]))
    return {
        "missing": missing[:200],
        "missing_count": len(missing),
        "distinct_links": len(targets),
        "scanned": len(rows),
        "sampled": sampled,
    }
