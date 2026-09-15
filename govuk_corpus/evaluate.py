"""AI inclusion-pass evaluation of a category's shortlist, tracked per run.

Each run has a fixed model/provider, so different models can be compared over the
same shortlist. A run's per-page decisions live in `evaluation_results` keyed by
(run_id, url); run totals (pages, kept, dropped, cost, time) live in
`evaluation_runs`. Prompt building + response parsing are pure (testable); the web
app makes the API calls and drives runs.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from typing import Dict, List, Optional, Sequence

from .backend import db
from . import shortlist

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

BODY_CHAR_LIMIT = 6000   # ~1.5k tokens of body sent to the model

PHASE_INCLUSION = "Phase 1 - Inclusion"
PHASE_EXCLUSION = "Phase 2 - Exclusion"
PHASE_ADJUDICATION = "Phase 3 - Adjudication"


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
    m = re.search(r"\{.*\}", t, re.DOTALL)
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


# ---- runs ----------------------------------------------------------------
def create_run(conn, category_id: int, model: str, provider: str,
               phase: str = PHASE_INCLUSION, name: Optional[str] = None) -> str:
    run_id = f"run_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:6]}"
    if not name:
        n = conn.execute(f"SELECT COUNT(*) AS c FROM evaluation_runs WHERE category_id = {_P}",
                         (category_id,)).fetchone()["c"]
        name = f"Test-{n + 1}"
    conn.execute(
        f"INSERT INTO evaluation_runs (run_id, category_id, name, phase, model, provider, started_at) "
        f"VALUES ({_P},{_P},{_P},{_P},{_P},{_P},{_P})",
        (run_id, category_id, name, phase, model, provider, db.now_iso()))
    conn.commit()
    return run_id


def rename_run(conn, run_id: str, name: str) -> None:
    conn.execute(f"UPDATE evaluation_runs SET name = {_P} WHERE run_id = {_P}",
                 (name.strip(), run_id))
    conn.commit()


def run_candidates(conn, run_id: str, category_id: int, limit: int, *,
                   organisations: Sequence[str], document_types: Sequence[str] = (),
                   keywords: Sequence[str] = (), match: str = "any") -> List[dict]:
    """Next `limit` shortlisted pages not yet evaluated IN THIS RUN."""
    select_expr = ("c.url AS url, c.title AS title, c.description AS description, "
                   "c.search_text AS body")
    extra_where = (f"c.url NOT IN (SELECT url FROM evaluation_results WHERE run_id = {_P})")
    sql, params = shortlist.build_query(
        select_expr=select_expr, extra_where=extra_where, extra_params=[run_id],
        organisations=organisations, document_types=document_types,
        keywords=keywords, match=match, limit=limit)
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def save_page(conn, run_id: str, category_id: int, url: str,
              decision: Optional[Dict], ms: int) -> None:
    keep = decision.get("keep") if decision else None
    score = decision.get("score") if decision else None
    reason = decision.get("reason") if decision else "unparseable model reply"
    conn.execute(
        f"INSERT INTO evaluation_results (run_id, category_id, url, keep, score, reason, ms, created_at) "
        f"VALUES ({_P},{_P},{_P},{_P},{_P},{_P},{_P},{_P})",
        (run_id, category_id, url, keep, score, reason, ms, db.now_iso()))
    # update run totals
    kept = 1 if keep == 1 else 0
    dropped = 1 if keep == 0 else 0
    unpar = 1 if keep is None else 0
    conn.execute(
        f"UPDATE evaluation_runs SET pages = pages + 1, kept = kept + {_P}, "
        f"dropped = dropped + {_P}, unparseable = unparseable + {_P}, total_ms = total_ms + {_P} "
        f"WHERE run_id = {_P}", (kept, dropped, unpar, ms, run_id))
    conn.commit()


def set_actual_model(conn, run_id: str, actual_model: str) -> None:
    """Record the model the API actually served (once), if we don't have it yet."""
    if not actual_model:
        return
    conn.execute(
        f"UPDATE evaluation_runs SET actual_model = {_P} "
        f"WHERE run_id = {_P} AND (actual_model IS NULL OR actual_model = '')",
        (actual_model, run_id))
    conn.commit()


def add_run_cost(conn, run_id: str, cost: float,
                 in_tokens: Optional[int] = None, out_tokens: Optional[int] = None,
                 hit_tokens: Optional[int] = None, miss_tokens: Optional[int] = None) -> None:
    """Accumulate this call's cost and (optionally) its token counts, including the
    cache hit/miss split of the input tokens."""
    conn.execute(
        f"UPDATE evaluation_runs SET cost = cost + {_P}, "
        f"in_tokens = in_tokens + {_P}, out_tokens = out_tokens + {_P}, "
        f"hit_tokens = hit_tokens + {_P}, miss_tokens = miss_tokens + {_P} WHERE run_id = {_P}",
        (cost or 0.0, in_tokens or 0, out_tokens or 0, hit_tokens or 0, miss_tokens or 0, run_id))
    conn.commit()


def finish_run(conn, run_id: str) -> None:
    conn.execute(f"UPDATE evaluation_runs SET finished_at = {_P} WHERE run_id = {_P}",
                 (db.now_iso(), run_id))
    conn.commit()


def get_run(conn, run_id: str) -> Optional[dict]:
    row = conn.execute(f"SELECT * FROM evaluation_runs WHERE run_id = {_P}", (run_id,)).fetchone()
    return dict(row) if row else None


def delete_run(conn, run_id: str) -> None:
    """Delete a run and its per-page results. Does NOT touch the ai_usage spend ledger,
    so the daily budget accounting is preserved."""
    conn.execute(f"DELETE FROM evaluation_results WHERE run_id = {_P}", (run_id,))
    conn.execute(f"DELETE FROM evaluation_runs WHERE run_id = {_P}", (run_id,))
    conn.commit()


def list_runs(conn, category_id: int) -> List[dict]:
    rows = conn.execute(
        f"SELECT * FROM evaluation_runs WHERE category_id = {_P} ORDER BY started_at DESC",
        (category_id,)).fetchall()
    return [dict(r) for r in rows]


def compare(conn, base_run: str, other_run: str) -> Dict[str, int]:
    """Compare `other_run` to `base_run` over pages evaluated in BOTH: how many the
    other run kept/dropped, and how many decisions disagree with the base run."""
    sql = (
        "SELECT COUNT(*) AS shared, "
        "SUM(CASE WHEN o.keep = 1 THEN 1 ELSE 0 END) AS kept, "
        "SUM(CASE WHEN o.keep = 0 THEN 1 ELSE 0 END) AS dropped, "
        "SUM(CASE WHEN o.keep = b.keep THEN 0 "
        "         WHEN o.keep IS NULL AND b.keep IS NULL THEN 0 ELSE 1 END) AS disagree "
        "FROM evaluation_results b JOIN evaluation_results o ON o.url = b.url "
        f"WHERE b.run_id = {_P} AND o.run_id = {_P}")
    row = conn.execute(sql, (base_run, other_run)).fetchone()
    return {"shared": row["shared"] or 0, "kept": row["kept"] or 0,
            "dropped": row["dropped"] or 0, "disagree": row["disagree"] or 0}


def run_results(conn, run_id: str, keep: Optional[int] = None,
                limit: Optional[int] = None) -> List[dict]:
    sql = f"SELECT url, keep, score, reason FROM evaluation_results WHERE run_id = {_P}"
    params: list = [run_id]
    if keep is not None:
        sql += f" AND keep = {_P}"
        params.append(keep)
    sql += " ORDER BY score DESC NULLS LAST, url" if _IS_PG else " ORDER BY score DESC, url"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
