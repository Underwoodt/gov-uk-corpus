"""Audit-dashboard aggregates for a funnel stage.

Given a filter set (organisations / document_types / keywords), computes: page count,
average body size / reading age / GDS issue count, a freshness histogram (by
public_updated_at), and the top individual GDS issues parsed out of gds_findings.

Freshness uses lexical comparison of the ISO public_updated_at text against ISO date
thresholds — no date casting, so it is backend-agnostic and safe on malformed values.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from .backend import db
from . import shortlist

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

FRESHNESS_BUCKETS = ["< 1 month", "1–3 months", "3 months–1 year",
                     "1–2 years", "> 2 years", "Unknown"]

# "token (count)" pairs inside a findings summary, e.g. "in order to (3)".
_ISSUE_RE = re.compile(r"([A-Za-z][A-Za-z' \-]*?)\s*\((\d+)\)")
_OVERLONG_RE = re.compile(r"Over-long sentences[^:]*:\s*(\d+)")


def _where(organisations, document_types, keywords, match):
    """(where_sql, params) mirroring shortlist.build_query's predicates."""
    where, params = [], []
    if organisations:
        ph = ",".join([_P] * len(organisations))
        where.append(f"EXISTS (SELECT 1 FROM page_organisations po "
                     f"WHERE po.page_url = c.url AND po.organisation_slug IN ({ph}))")
        params.extend(organisations)
    if document_types:
        ph = ",".join([_P] * len(document_types))
        where.append(f"c.document_type IN ({ph})")
        params.extend(document_types)
    if keywords:
        clause, kwp = shortlist._keyword_clause(keywords, match, _IS_PG)
        where.append(clause)
        params.extend(kwp)
    where.append("c.is_redirect = 0")
    where.append("c.content_hash IS NOT NULL")
    return " WHERE " + " AND ".join(where), params


def parse_issues(findings: str) -> Counter:
    """Individual GDS issues + counts from one page's findings summary."""
    c: Counter = Counter()
    if not findings:
        return c
    for token, n in _ISSUE_RE.findall(findings):
        c[token.strip()] += int(n)
    m = _OVERLONG_RE.search(findings)
    if m:
        c["Over-long sentences"] += int(m.group(1))
    return c


def stats(conn, *, organisations: Sequence[str] = (), document_types: Sequence[str] = (),
          keywords: Sequence[str] = (), match: str = "any",
          now: Optional[datetime] = None, sample_limit: int = 25000) -> Dict:
    where_sql, wparams = _where(organisations, document_types, keywords, match)
    now = now or datetime.now(timezone.utc)
    iso = lambda days: (now - timedelta(days=days)).strftime("%Y-%m-%d")
    t1, t3, t12, t24 = iso(30), iso(91), iso(365), iso(730)
    size = shortlist._SIZE_EXPR

    agg_sql = (
        f"SELECT COUNT(*) AS n, "
        f"AVG({size}) AS avg_size, AVG(c.reading_age) AS avg_reading_age, "
        f"AVG(c.gds_english_score) AS avg_gds, "
        f"SUM(CASE WHEN c.public_updated_at >= {_P} THEN 1 ELSE 0 END) AS f0, "
        f"SUM(CASE WHEN c.public_updated_at < {_P} AND c.public_updated_at >= {_P} THEN 1 ELSE 0 END) AS f1, "
        f"SUM(CASE WHEN c.public_updated_at < {_P} AND c.public_updated_at >= {_P} THEN 1 ELSE 0 END) AS f2, "
        f"SUM(CASE WHEN c.public_updated_at < {_P} AND c.public_updated_at >= {_P} THEN 1 ELSE 0 END) AS f3, "
        f"SUM(CASE WHEN c.public_updated_at < {_P} THEN 1 ELSE 0 END) AS f4, "
        f"SUM(CASE WHEN c.public_updated_at IS NULL OR c.public_updated_at = '' THEN 1 ELSE 0 END) AS f5 "
        f"FROM content c{where_sql}")
    agg_params = [t1, t1, t3, t3, t12, t12, t24, t24] + wparams
    row = dict(conn.execute(agg_sql, tuple(agg_params)).fetchone())

    freshness = [
        {"bucket": FRESHNESS_BUCKETS[0], "count": int(row["f0"] or 0)},
        {"bucket": FRESHNESS_BUCKETS[1], "count": int(row["f1"] or 0)},
        {"bucket": FRESHNESS_BUCKETS[2], "count": int(row["f2"] or 0)},
        {"bucket": FRESHNESS_BUCKETS[3], "count": int(row["f3"] or 0)},
        {"bucket": FRESHNESS_BUCKETS[4], "count": int(row["f4"] or 0)},
        {"bucket": FRESHNESS_BUCKETS[5], "count": int(row["f5"] or 0)},
    ]

    # Top issues: parse findings in Python over the filtered pages that have any
    # (capped, so a huge stage stays bounded — flagged as sampled when the cap bites).
    find_sql = (f"SELECT c.gds_findings AS f FROM content c{where_sql} "
                f"AND c.gds_findings IS NOT NULL AND c.gds_findings <> '' "
                f"ORDER BY c.url LIMIT {int(sample_limit) + 1}")
    rows = conn.execute(find_sql, tuple(wparams)).fetchall()
    sampled = len(rows) > sample_limit
    tally: Counter = Counter()
    for r in rows[:sample_limit]:
        tally.update(parse_issues(r["f"]))
    top_issues = [{"issue": k, "count": v} for k, v in tally.most_common(10)]

    def num(x):
        return round(float(x), 1) if x is not None else None

    return {
        "n": int(row["n"] or 0),
        "avg_size": num(row["avg_size"]),
        "avg_reading_age": num(row["avg_reading_age"]),
        "avg_gds": num(row["avg_gds"]),
        "freshness": freshness,
        "top_issues": top_issues,
        "issues_sampled": sampled,
        "issues_scanned": min(len(rows), sample_limit),
    }
