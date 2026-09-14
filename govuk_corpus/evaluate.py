"""AI inclusion-pass evaluation of a category's shortlisted pages.

Single pass per page: given the category's include/exclude context and the page
(title + description + truncated body), the model decides keep/drop with a score
and a short reason. Prompt building + response parsing live here (pure, testable);
the web app makes the API call and stores results in `category_evaluation`.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Sequence

from .backend import db
from . import shortlist

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

BODY_CHAR_LIMIT = 6000   # ~1.5k tokens of body sent to the model


def build_prompt(inclusion: str, exclusion: str, title: str, description: str,
                 body: str, body_limit: int = BODY_CHAR_LIMIT) -> str:
    body = (body or "")[:body_limit]
    return (
        "You are assessing whether a GOV.UK page is relevant to a topic.\n\n"
        "Topic to INCLUDE (keep pages about this):\n"
        f"{inclusion.strip() or '(not specified)'}\n\n"
        "EXCLUDE (looks relevant but is not):\n"
        f"{exclusion.strip() or '(none given)'}\n\n"
        f"Page title: {title or '(none)'}\n"
        f"Page description: {description or '(none)'}\n\n"
        "Page content (may be truncated):\n"
        f"{body or '(no body text)'}\n\n"
        "Decide whether to KEEP this page for the topic. A page is relevant if it "
        "concerns the topic in the sense described, even if only part of the page does. "
        "Drop it if it is out of scope or matches an exclusion.\n\n"
        "Return ONLY a JSON object, no prose:\n"
        '{"keep": true|false, "score": 0.0-1.0, "reason": "1-2 sentence explanation"}'
    )


def parse_decision(text: str) -> Optional[Dict]:
    """Parse the model reply into {keep, score, reason}, or None if unparseable."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)   # first JSON object
    if m:
        t = m.group(0)
    try:
        d = json.loads(t)
    except (ValueError, TypeError):
        return None
    keep = d.get("keep")
    try:
        score = float(d.get("score")) if d.get("score") is not None else None
    except (ValueError, TypeError):
        score = None
    return {"keep": 1 if keep else 0 if keep is not None else None,
            "score": score, "reason": str(d.get("reason") or "")[:1000]}


def candidates(conn, category_id: int, limit: int, *, organisations: Sequence[str],
               document_types: Sequence[str] = (), keywords: Sequence[str] = (),
               match: str = "any") -> List[dict]:
    """Next `limit` shortlisted pages not yet evaluated (url, title, description, body)."""
    select_expr = ("c.url AS url, c.title AS title, c.description AS description, "
                   "c.search_text AS body")
    extra_where = (f"c.url NOT IN (SELECT url FROM category_evaluation "
                   f"WHERE category_id = {_P})")
    sql, params = shortlist.build_query(
        select_expr=select_expr, extra_where=extra_where, extra_params=[category_id],
        organisations=organisations, document_types=document_types,
        keywords=keywords, match=match, limit=limit)
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def save_result(conn, category_id: int, url: str, decision: Optional[Dict], model: str) -> None:
    keep = decision.get("keep") if decision else None
    score = decision.get("score") if decision else None
    reason = (decision.get("reason") if decision else "unparseable model reply")
    ts = db.now_iso()
    row = (category_id, url, keep, score, reason, model, ts)
    if _IS_PG:
        conn.execute(
            "INSERT INTO category_evaluation (category_id, url, keep, score, reason, model, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (category_id, url) DO UPDATE SET "
            "keep=EXCLUDED.keep, score=EXCLUDED.score, reason=EXCLUDED.reason, "
            "model=EXCLUDED.model, created_at=EXCLUDED.created_at", row)
    else:
        conn.execute(
            "INSERT OR REPLACE INTO category_evaluation "
            "(category_id, url, keep, score, reason, model, created_at) VALUES (?,?,?,?,?,?,?)", row)
    conn.commit()


def summary(conn, category_id: int) -> Dict[str, int]:
    rows = conn.execute(
        f"SELECT keep, COUNT(*) AS n FROM category_evaluation WHERE category_id = {_P} "
        f"GROUP BY keep", (category_id,)).fetchall()
    counts = {r["keep"]: r["n"] for r in rows}
    return {"evaluated": sum(counts.values()),
            "kept": counts.get(1, 0), "dropped": counts.get(0, 0),
            "unparseable": counts.get(None, 0)}


def results(conn, category_id: int, keep: Optional[int] = None,
            limit: Optional[int] = None) -> List[dict]:
    sql = (f"SELECT url, keep, score, reason FROM category_evaluation "
           f"WHERE category_id = {_P}")
    params: list = [category_id]
    if keep is not None:
        sql += f" AND keep = {_P}"
        params.append(keep)
    sql += " ORDER BY score DESC NULLS LAST, url" if _IS_PG else " ORDER BY score DESC, url"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
