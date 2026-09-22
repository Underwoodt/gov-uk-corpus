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
import os
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from .backend import db
from . import shortlist

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

BODY_CHAR_LIMIT = 20000   # ~5k tokens of body; covers ~97% of pages in full (median page ~600 chars)

_TRUNC_MARKER = "\n\n…[middle of page trimmed]…\n\n"


def truncate_body(body: str, limit: int = BODY_CHAR_LIMIT) -> str:
    """Fit a page body into `limit` chars, keeping the HEAD and the TAIL (dropping the middle)
    when it's too long — so a long page's ending (summaries, related links, the actual topic
    if it appears late) isn't lost the way a plain head-only cut loses it. Short pages are
    returned unchanged."""
    body = body or ""
    if len(body) <= limit:
        return body
    keep = limit - len(_TRUNC_MARKER)
    if keep <= 0:                       # pathologically small limit: just take the head
        return body[:limit]
    head = (keep * 2) // 3              # 2:1 head:tail — the topic is usually introduced early
    tail = keep - head
    return body[:head] + _TRUNC_MARKER + body[len(body) - tail:]

PHASE_INCLUSION = "Phase 1 - Inclusion"
PHASE_EXCLUSION = "Phase 2 - Exclusion"
PHASE_ADJUDICATION = "Phase 3 - Adjudication"

# Execution mode for a phase's inference. Synchronous = one blocking API call per
# page (the current behaviour). Batch = submit the whole phase as one asynchronous
# job via the Anthropic Message Batches API (~50% cheaper; Anthropic models only —
# DeepSeek's endpoint has no batch API, so a DeepSeek phase always runs synchronously).
MODE_SYNC = "synchronous"
MODE_BATCH = "batch"
PHASE_MODES = (MODE_SYNC, MODE_BATCH)


def normalise_mode(value: Optional[str]) -> str:
    """Coerce a stored/form value to a valid mode, defaulting to synchronous."""
    return MODE_BATCH if (value or "").strip().lower() == MODE_BATCH else MODE_SYNC


# Editable prompt templates. The wording lives here (and can be overridden by a saved version —
# see govuk_corpus/prompts.py); the {{PLACEHOLDERS}} are filled in per page by the build_* helpers.
DEFAULT_INCLUSION_TEMPLATE = (
    "You are assessing whether a GOV.UK page is relevant to a topic.\n\n"
    "Topic to INCLUDE (keep pages about this):\n{{INCLUDE}}\n\n"
    "Page title: {{TITLE}}\n"
    "Page description: {{DESCRIPTION}}\n\n"
    "Page content (may be truncated):\n{{BODY}}\n\n"
    "Decide whether to KEEP this page for the topic. A page is relevant if it "
    "concerns the topic in the sense described, even if only part of the page does. "
    "Mark it FALSE only when every mention is the wrong sense; otherwise keep it.\n\n"
    "Scoring guidance (amount, not the boolean):\n"
    "- 0.1–0.3: mentioned once or in passing — still TRUE if the sense is right\n"
    "- 0.4–0.6: discussed to a moderate extent\n"
    "- 0.7–1.0: a major focus of the page\n"
    "- 0.0: every hit is the wrong sense — the only case for FALSE\n\n"
    "Return ONLY a JSON object, no prose:\n"
    '{"keep": true|false, "score": 0.0-1.0, "reason": "1-2 sentence explanation"}')

DEFAULT_EXCLUSION_TEMPLATE = (
    "You curate a GOV.UK corpus for a {{NAME_UPPER}} audit. A fast first pass flagged this "
    "page because it looked relevant; it forces KEEP on any mention. Remove ONLY pages that "
    "are clearly not about {{NAME}} at all. Missing a genuinely {{NAME}}-relevant page is "
    "unacceptable; keeping a borderline one is fine. Default to KEEP.\n\n"
    "=== {{NAME_UPPER}} SPEC ===\n{{SPEC}}\n=== END SPEC ===\n\n"
    "Apply the inclusion and exclusion criteria above.\n"
    "KEEP (keep = true) — keep if the page has any audit-relevant {{NAME}} content, even briefly.\n"
    "DROP (keep = false) — drop ONLY when the page is clearly out of scope per the exclusion "
    "criteria (homonyms, incidental-only mentions, wrong domain).\n"
    "{{KEEP_SECTION}}{{DROP_SECTION}}"
    "If you are unsure, KEEP.\n\n"
    "For context, the first pass wrote this note (it may be wrong): {{PASS1_REASON}}\n\n"
    "{{TITLE_LINE}}\nPage content (may be truncated):\n{{BODY}}\n\n"
    "Return ONLY a JSON object, no prose:\n"
    '{"keep": true|false, "exclusion_hit": "none"|"incidental"|"homonym"|"wrong_domain", '
    '"reason": "1-2 sentence explanation"}')


# ---- Cached prompt variant (A/B trial) -----------------------------------
# The same wording, reordered so ALL the stable instruction text comes first and the per-page
# content comes last, separated by PROMPT_SPLIT. The caller sends the stable half as the (cacheable)
# system prompt and the page half as the user message — so a run's repeated prefix is a cache-read
# on Anthropic, and DeepSeek's automatic prefix cache kicks in too. `current` variant is unchanged.
PROMPT_SPLIT = "\n\n<<<PAGE>>>\n\n"

DEFAULT_INCLUSION_TEMPLATE_CACHED = (
    "You are assessing whether a GOV.UK page is relevant to a topic.\n\n"
    "Topic to INCLUDE (keep pages about this):\n{{INCLUDE}}\n\n"
    "Decide whether to KEEP the page below for the topic. A page is relevant if it "
    "concerns the topic in the sense described, even if only part of the page does. "
    "Mark it FALSE only when every mention is the wrong sense; otherwise keep it.\n\n"
    "Scoring guidance (amount, not the boolean):\n"
    "- 0.1–0.3: mentioned once or in passing — still TRUE if the sense is right\n"
    "- 0.4–0.6: discussed to a moderate extent\n"
    "- 0.7–1.0: a major focus of the page\n"
    "- 0.0: every hit is the wrong sense — the only case for FALSE\n\n"
    "Return ONLY a JSON object, no prose:\n"
    '{"keep": true|false, "score": 0.0-1.0, "reason": "1-2 sentence explanation"}'
    + PROMPT_SPLIT +
    "Page title: {{TITLE}}\nPage description: {{DESCRIPTION}}\n\n"
    "Page content (may be truncated):\n{{BODY}}")

DEFAULT_EXCLUSION_TEMPLATE_CACHED = (
    "You curate a GOV.UK corpus for a {{NAME_UPPER}} audit. A fast first pass flagged this "
    "page because it looked relevant; it forces KEEP on any mention. Remove ONLY pages that "
    "are clearly not about {{NAME}} at all. Missing a genuinely {{NAME}}-relevant page is "
    "unacceptable; keeping a borderline one is fine. Default to KEEP.\n\n"
    "=== {{NAME_UPPER}} SPEC ===\n{{SPEC}}\n=== END SPEC ===\n\n"
    "Apply the inclusion and exclusion criteria above.\n"
    "KEEP (keep = true) — keep if the page has any audit-relevant {{NAME}} content, even briefly.\n"
    "DROP (keep = false) — drop ONLY when the page is clearly out of scope per the exclusion "
    "criteria (homonyms, incidental-only mentions, wrong domain).\n"
    "{{KEEP_SECTION}}{{DROP_SECTION}}"
    "If you are unsure, KEEP.\n\n"
    "Return ONLY a JSON object, no prose:\n"
    '{"keep": true|false, "exclusion_hit": "none"|"incidental"|"homonym"|"wrong_domain", '
    '"reason": "1-2 sentence explanation"}'
    + PROMPT_SPLIT +
    "For context, the first pass wrote this note (it may be wrong): {{PASS1_REASON}}\n\n"
    "{{TITLE_LINE}}\nPage content (may be truncated):\n{{BODY}}")


def cached_template(name: str) -> str:
    """The reordered (stable-prefix) default template for a phase — 'inclusion' | 'exclusion'."""
    return DEFAULT_EXCLUSION_TEMPLATE_CACHED if name == "exclusion" else DEFAULT_INCLUSION_TEMPLATE_CACHED


def split_cached(filled: str):
    """(stable, variable) for a filled cached template. If it carries no PROMPT_SPLIT marker (e.g.
    a non-cached template), the whole thing is the variable half with an empty stable prefix."""
    stable, sep, variable = filled.partition(PROMPT_SPLIT)
    return (stable, variable) if sep else ("", filled)


def _fill(template: str, values: Dict[str, str]) -> str:
    """Substitute {{KEY}} tokens (str.replace, so literal { } in the text are left alone)."""
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def build_prompt(inclusion: str, exclusion: str, title: str, description: str,
                 body: str, body_limit: int = BODY_CHAR_LIMIT, template: Optional[str] = None) -> str:
    body = truncate_body(body, body_limit)
    return _fill(template or DEFAULT_INCLUSION_TEMPLATE, {
        "INCLUDE": inclusion.strip() or "(not specified)",
        "EXCLUDE": exclusion.strip() or "(none given)",
        "TITLE": title or "(none)",
        "DESCRIPTION": description or "(none)",
        "BODY": body or "(no body text)",
    })


def _iter_json_spans(text: str):
    """Yield each top-level balanced ``{...}`` substring, string/escape aware — so a ``}``
    inside a quoted value doesn't end the span, and reasoning prose around the object is
    skipped rather than swept in."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start:i + 1]
                start = -1


def _extract_json_object(text: str, prefer_keys=("keep",)) -> Optional[Dict]:
    """The model's decision object, recovered even when wrapped in reasoning/prose.

    Tries the whole reply first, then each balanced ``{...}`` span it contains, and returns the
    LAST valid object that carries an expected key — a reasoning model tends to emit its verdict
    after the reasoning. Replaces a greedy first-'{'-to-last-'}' regex that grabbed prose (and
    any stray braces in it) and then failed to parse."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    candidates: List[Dict] = []
    try:
        obj = json.loads(t)                       # clean reply: whole thing is the object
        if isinstance(obj, dict):
            candidates.append(obj)
    except (ValueError, TypeError):
        pass
    if not candidates:                            # prose-wrapped: scan for embedded objects
        for span in _iter_json_spans(t):
            try:
                obj = json.loads(span)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                candidates.append(obj)
    if not candidates:
        return None
    for obj in reversed(candidates):
        if any(k in obj for k in prefer_keys):
            return obj
    return candidates[-1]


def parse_decision(text: str) -> Optional[Dict]:
    """Parse the model reply into {keep, score, reason}, or None if unparseable."""
    d = _extract_json_object(text, prefer_keys=("keep",))
    if d is None:
        return None
    keep = d.get("keep")
    try:
        score = float(d.get("score")) if d.get("score") is not None else None
    except (ValueError, TypeError):
        score = None
    return {"keep": 1 if keep else 0 if keep is not None else None,
            "score": score, "reason": str(d.get("reason") or "")[:1000]}


# Inclusion-score bands -> a plain-English confidence label, matching the scoring rubric in
# DEFAULT_INCLUSION_TEMPLATE (0 = wrong sense; 0.1-0.3 in passing; 0.4-0.6 moderate; 0.7-1.0 major).
def confidence_label(score) -> str:
    """The confidence label for an inclusion score (how central the topic is to the page).
    Empty string when there's no score (page not evaluated / unparseable)."""
    if score is None:
        return ""
    try:
        s = float(score)
    except (ValueError, TypeError):
        return ""
    if s <= 0:
        return "Wrong sense"
    if s <= 0.35:
        return "Mentioned in passing"
    if s <= 0.65:
        return "Discussed a moderate amount"
    return "Major focus"


# ---- Phase 2: Exclusion --------------------------------------------------
# Recall-priority second pass over the pages the inclusion run KEPT. It re-introduces
# the exclusion criteria and only ever turns a keep into a drop (removing false
# positives). Adapted from the DEFRA guidance-relevance-filter adjudication pass.
def build_exclusion_prompt(name: str, inclusion: str, exclusion: str,
                           keep_hints: str, drop_hints: str, title: str, body: str,
                           pass1_reason: str, body_limit: int = BODY_CHAR_LIMIT,
                           template: Optional[str] = None) -> str:
    body = truncate_body(body, body_limit)
    nm = (name or "the topic").strip()
    # SPEC labels both halves so the model isn't left inferring which block is which; the
    # exclusion half always shows, with (none given) when no exclusion text was provided.
    spec = ("Inclusion criteria:\n" + ((inclusion or "").strip() or "(not specified)")
            + "\n\nExclusion criteria:\n" + ((exclusion or "").strip() or "(none given)"))
    keep_section = ""
    if (keep_hints or "").strip():
        keep_section = ("\nKEEP examples (keep = true) — lean toward keeping when similar "
                        f"content appears:\n{keep_hints.strip()}\n")
    drop_section = ""
    if (drop_hints or "").strip():
        drop_section = ("\nDROP examples (keep = false) — drop only when clearly similar to:\n"
                        f"{drop_hints.strip()}\n")
    title_line = f"Page title: {title}\n" if title else ""
    return _fill(template or DEFAULT_EXCLUSION_TEMPLATE, {
        "NAME_UPPER": nm.upper(),
        "NAME": nm,
        "SPEC": spec,
        "KEEP_SECTION": keep_section,
        "DROP_SECTION": drop_section,
        "PASS1_REASON": repr(pass1_reason),
        "TITLE_LINE": title_line,
        "BODY": body or "(no body text)",
    })


def parse_exclusion(text: str) -> Optional[Dict]:
    """Parse an exclusion reply into a save_page decision {keep, score, reason}, tagging
    the reason with the exclusion_hit category. None if unparseable/missing verdict."""
    d = _extract_json_object(text, prefer_keys=("keep",))
    if d is None:
        return None
    keep = d.get("keep")
    if keep is None:
        return None
    hit = str(d.get("exclusion_hit") or "").strip()
    reason = str(d.get("reason") or d.get("verdict_reason") or "")[:1000]
    if hit and hit.lower() != "none":
        reason = f"[{hit}] {reason}".strip()
    return {"keep": 1 if keep else 0, "score": None, "reason": reason}


# The model's exclusion_hit categories -> the display label shown as a header line above the
# reason. parse_exclusion (above) tags an excluded page's stored reason with a leading [hit];
# an untagged reason is a page that survived the exclusion pass -> "KEPT" (exclusion_hit "none").
EXCLUSION_HIT_LABELS = {
    "incidental": "Excluded as Incidental",
    "homonym": "Excluded as homonym",
    "wrong_domain": "Excluded as wrong domain",
}


def exclusion_label(reason) -> "tuple[str, str]":
    """(label, clean_reason) for a stored exclusion-run reason: map its leading [hit] tag to the
    display label and strip the tag; an untagged reason -> ("KEPT", reason unchanged)."""
    r = str(reason or "")
    m = re.match(r"\s*\[([a-z_]+)\]\s*", r, re.I)
    if m:
        label = EXCLUSION_HIT_LABELS.get(m.group(1).lower())
        if label:
            return label, r[m.end():]
    return "KEPT", r


def exclusion_display(reason):
    """'label\\nclean_reason' for a non-empty exclusion reason (header line + body), left as-is
    (falsy) when there is no exclusion reason. Single source for the funnel/audit tables and
    the CSV/XLSX/JSON exports so every surface shows the same exclusion_hit label."""
    if not reason:
        return reason
    label, clean = exclusion_label(reason)
    return f"{label}\n{clean}"


def latest_inclusion_run(conn, category_id: int) -> Optional[str]:
    """The most recent Phase-1 inclusion run for a category (its keeps feed exclusion)."""
    row = conn.execute(
        f"SELECT run_id FROM evaluation_runs WHERE category_id = {_P} AND phase = {_P} "
        f"ORDER BY started_at DESC LIMIT 1", (category_id, PHASE_INCLUSION)).fetchone()
    return row["run_id"] if row else None


def latest_exclusion_run(conn, source_run_id: str) -> Optional[str]:
    """The most recent Phase-2 exclusion run built on a given inclusion run."""
    row = conn.execute(
        f"SELECT run_id FROM evaluation_runs WHERE source_run_id = {_P} AND phase = {_P} "
        f"ORDER BY started_at DESC LIMIT 1", (source_run_id, PHASE_EXCLUSION)).fetchone()
    return row["run_id"] if row else None


def kept_count(conn, run_id: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) AS c FROM evaluation_results WHERE run_id = {_P} AND keep = 1",
        (run_id,)).fetchone()["c"]


def exclusion_candidates(conn, run_id: str, source_run_id: str, limit: int) -> List[dict]:
    """Next `limit` pages the source (inclusion) run kept that this exclusion run has not
    yet re-evaluated. Carries the inclusion pass's reason as pass1_reason."""
    sql = (
        "SELECT c.url AS url, c.title AS title, c.description AS description, "
        "c.search_text AS body, r.reason AS pass1_reason "
        "FROM evaluation_results r JOIN content c ON c.url = r.url "
        f"WHERE r.run_id = {_P} AND r.keep = 1 "
        f"AND r.url NOT IN (SELECT url FROM evaluation_results WHERE run_id = {_P}) "
        f"ORDER BY r.url LIMIT {_P}")
    return [dict(x) for x in conn.execute(sql, (source_run_id, run_id, limit)).fetchall()]


# ---- runs ----------------------------------------------------------------
def create_run(conn, category_id: int, model: str, provider: str,
               phase: str = PHASE_INCLUSION, name: Optional[str] = None,
               source_run_id: Optional[str] = None, prompt_spec: Optional[str] = None) -> str:
    """`prompt_spec` is a JSON snapshot of the prompt inputs this run will use (template +
    version + Include/Exclude context + hints), stamped so the run's prompts are exactly
    reproducible later even if the active template or the shortlist definition changes."""
    run_id = f"run_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:6]}"
    if not name:
        n = conn.execute(f"SELECT COUNT(*) AS c FROM evaluation_runs WHERE category_id = {_P}",
                         (category_id,)).fetchone()["c"]
        name = f"Test-{n + 1}"
    conn.execute(
        f"INSERT INTO evaluation_runs (run_id, category_id, name, source_run_id, phase, model, provider, started_at, prompt_spec) "
        f"VALUES ({_P},{_P},{_P},{_P},{_P},{_P},{_P},{_P},{_P})",
        (run_id, category_id, name, source_run_id, phase, model, provider, db.now_iso(), prompt_spec))
    conn.commit()
    return run_id


def run_prompt_spec(conn, run_id: str) -> Optional[Dict]:
    """The stamped prompt spec for a run (the template + context it used), or None for a
    legacy run that predates stamping."""
    row = conn.execute(f"SELECT prompt_spec FROM evaluation_runs WHERE run_id = {_P}",
                       (run_id,)).fetchone()
    raw = dict(row).get("prompt_spec") if row else None
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except (ValueError, TypeError):
        return None


def rename_run(conn, run_id: str, name: str) -> None:
    conn.execute(f"UPDATE evaluation_runs SET name = {_P} WHERE run_id = {_P}",
                 (name.strip(), run_id))
    conn.commit()


def run_candidates(conn, run_id: str, category_id: int, limit: int, *,
                   organisations: Sequence[str], document_types: Sequence[str] = (),
                   keywords: Sequence[str] = (), match: str = "any",
                   include_search_only: bool = True) -> List[dict]:
    """Next `limit` shortlisted pages not yet evaluated IN THIS RUN. The deterministic
    shortlist is evaluated first; if there's room left, top up with the category's pinned
    GOV.UK-Search-only pages that have since been fetched into the corpus (so search-only
    coverage gaps are evaluated too)."""
    select_expr = ("c.url AS url, c.title AS title, c.description AS description, "
                   "c.search_text AS body")
    extra_where = (f"c.url NOT IN (SELECT url FROM evaluation_results WHERE run_id = {_P})")
    sql, params = shortlist.build_query(
        select_expr=select_expr, extra_where=extra_where, extra_params=[run_id],
        organisations=organisations, document_types=document_types,
        keywords=keywords, match=match, limit=limit)
    rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    if not include_search_only or len(rows) >= limit:
        return rows[:limit]
    # Top up with pinned GOV.UK-Search-only pages that are now in the corpus (have a body)
    # and not yet evaluated in this run.
    got = {r["url"] for r in rows}
    top = conn.execute(
        f"SELECT c.url AS url, c.title AS title, c.description AS description, c.search_text AS body "
        f"FROM category_search_pages sp JOIN content c ON c.url = sp.url "
        f"WHERE sp.category_id = {_P} AND sp.source = 'search' "
        f"AND c.is_redirect = 0 AND c.content_hash IS NOT NULL "
        f"AND c.url NOT IN (SELECT url FROM evaluation_results WHERE run_id = {_P}) "
        f"ORDER BY c.url LIMIT {_P}",
        (category_id, run_id, limit - len(rows))).fetchall()
    for r in top:
        d = dict(r)
        if d["url"] not in got:
            rows.append(d)
    return rows[:limit]


def save_page(conn, run_id: str, category_id: int, url: str,
              decision: Optional[Dict], ms: int, raw_reply: Optional[str] = None) -> None:
    keep = decision.get("keep") if decision else None
    score = decision.get("score") if decision else None
    reason = decision.get("reason") if decision else "unparseable model reply"
    # Keep the model's raw reply verbatim so an unparseable/odd decision can be debugged
    # later (capped so a runaway reply can't bloat the row).
    raw = (raw_reply or "")[:8000] or None
    conn.execute(
        f"INSERT INTO evaluation_results (run_id, category_id, url, keep, score, reason, raw_reply, ms, created_at) "
        f"VALUES ({_P},{_P},{_P},{_P},{_P},{_P},{_P},{_P},{_P})",
        (run_id, category_id, url, keep, score, reason, raw, ms, db.now_iso()))
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
    conn.execute(
        f"UPDATE evaluation_runs SET finished_at = {_P}, run_status = 'complete', pid = NULL, "
        f"heartbeat_at = {_P} WHERE run_id = {_P}",
        (db.now_iso(), db.now_iso(), run_id))
    conn.commit()


# ---- live run state (is a driver actively working this run?) --------------
# A run record carries run_status ('running'|'stopped'|'complete'), the driver's pid + host,
# and a heartbeat. The driver marks it running (updating the heartbeat) as it works, and marks
# it stopped when it steps away without finishing. If a run is left 'running' but its process
# is gone (crash, kill, restart), run_is_alive() reports it dead so the UI can offer to resume.
def mark_run_running(conn, run_id: str, pid: Optional[int] = None) -> None:
    conn.execute(
        f"UPDATE evaluation_runs SET run_status = 'running', pid = {_P}, host = {_P}, "
        f"heartbeat_at = {_P} WHERE run_id = {_P}",
        (int(pid if pid is not None else os.getpid()), socket.gethostname(), db.now_iso(), run_id))
    conn.commit()


def mark_run_stopped(conn, run_id: str) -> None:
    """Driver stepped away without finishing (budget/cap/error/stop). Leaves finished_at as-is."""
    conn.execute(
        f"UPDATE evaluation_runs SET run_status = 'stopped', pid = NULL, heartbeat_at = {_P} "
        f"WHERE run_id = {_P} AND finished_at IS NULL", (db.now_iso(), run_id))
    conn.commit()


def _heartbeat_age_seconds(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError):
        return None


def run_is_alive(run: dict, stale_after_s: int = 300) -> bool:
    """True if a driver is actually working this run right now. On the same host we trust the
    pid (os.kill(pid, 0)); off-host (or no pid) we fall back to a fresh heartbeat."""
    if not run or run.get("run_status") != "running":
        return False
    pid, host = run.get("pid"), run.get("host")
    if pid and host and host == socket.gethostname():
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError, TypeError):
            return False
    age = _heartbeat_age_seconds(run.get("heartbeat_at"))
    return age is not None and age < stale_after_s


def run_is_stalled(run: dict) -> bool:
    """An unfinished run that is marked running but whose driver is gone — safe to resume."""
    if not run or run.get("finished_at"):
        return False
    return run.get("run_status") == "running" and not run_is_alive(run)


def get_run(conn, run_id: str) -> Optional[dict]:
    row = conn.execute(f"SELECT * FROM evaluation_runs WHERE run_id = {_P}", (run_id,)).fetchone()
    return dict(row) if row else None


def delete_run(conn, run_id: str) -> None:
    """Delete a run and its per-page results. Does NOT touch the ai_usage spend ledger,
    so the daily budget accounting is preserved."""
    conn.execute(f"DELETE FROM evaluation_results WHERE run_id = {_P}", (run_id,))
    conn.execute(f"DELETE FROM evaluation_runs WHERE run_id = {_P}", (run_id,))
    conn.commit()


def list_runs_query(category_id: int):
    """(sql, params) for a category's runs list — so the page can show the SQL it ran."""
    return (f"SELECT * FROM evaluation_runs WHERE category_id = {_P} ORDER BY started_at DESC",
            [category_id])


def list_runs(conn, category_id: int) -> List[dict]:
    sql, params = list_runs_query(category_id)
    rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    for d in rows:
        spec = d.pop("prompt_spec", None)   # keep the list light; expose only the A/B trial knobs
        d["trial"] = None
        if spec:
            try:
                s = json.loads(spec)
                d["trial"] = {"variant": s.get("prompt_variant") or "current",
                              "concurrency": int(s.get("concurrency") or 1),
                              "caching": bool(s.get("caching")),
                              "template_version": s.get("template_version"),
                              "body_limit": s.get("body_limit")}
            except (ValueError, TypeError):
                pass
    return rows


def reconcile_to_shortlist(conn, category_id: int) -> dict:
    """Keep a category's LLM evaluation results aligned with its (freshly recomputed)
    shortlist membership after a definition change:

      * retain results for pages still in the shortlist (matched by content_id, so a page
        reached via a different url alias still counts),
      * drop results for pages the new filters removed,
      * new pages just aren't evaluated yet (the next run picks them up).

    Then recompute each run's pages/kept/dropped so the displayed counts stay honest
    (spend/token counters are left untouched — that cost was really incurred).
    Assumes category_shortlist_pages has already been refreshed. Best-effort."""
    conn.execute(
        f"DELETE FROM evaluation_results "
        f"WHERE run_id IN (SELECT run_id FROM evaluation_runs WHERE category_id = {_P}) "
        f"AND NOT EXISTS ("
        f"  SELECT 1 FROM content c "
        f"  JOIN category_shortlist_pages m ON m.content_id = COALESCE(c.content_id, c.url) "
        f"  WHERE c.url = evaluation_results.url AND m.category_id = {_P})",
        (category_id, category_id))
    conn.execute(
        f"UPDATE evaluation_runs SET "
        f"pages = (SELECT COUNT(*) FROM evaluation_results r WHERE r.run_id = evaluation_runs.run_id), "
        f"kept = (SELECT COUNT(*) FROM evaluation_results r WHERE r.run_id = evaluation_runs.run_id AND r.keep = 1), "
        f"dropped = (SELECT COUNT(*) FROM evaluation_results r WHERE r.run_id = evaluation_runs.run_id AND r.keep = 0) "
        f"WHERE category_id = {_P}",
        (category_id,))
    conn.commit()
    return {"ok": True}


def run_chain(conn, run_id: str) -> List[dict]:
    """The inclusion → exclusion (→ …) chain of runs this run belongs to, ordered
    oldest-first (so the inclusion phase comes first). Runs are linked by
    source_run_id; an evaluation's phases share one lineage."""
    start = get_run(conn, run_id)
    if not start:
        return []
    root = start
    for _ in range(20):                      # walk up to the lineage root
        parent_id = root.get("source_run_id")
        if not parent_id:
            break
        parent = get_run(conn, parent_id)
        if not parent:
            break
        root = parent
    chain = [root]
    ids = {root["run_id"]}
    pool = list_runs(conn, root["category_id"])
    added = True
    while added:                             # walk down, collecting descendants
        added = False
        for r in pool:
            if r["run_id"] not in ids and r.get("source_run_id") in ids:
                chain.append(r)
                ids.add(r["run_id"])
                added = True
    chain.sort(key=lambda r: (r.get("started_at") or ""))
    return chain


def unparsed_results(conn, run_ids: Sequence[str]) -> List[dict]:
    """The pages whose model reply could not be parsed (keep IS NULL) across the given
    runs — i.e. the items counted as 'unparseable'. Returns [{run_id, url, reason}]."""
    ids = list(run_ids)
    if not ids:
        return []
    ph = ",".join([_P] * len(ids))
    rows = conn.execute(
        f"SELECT run_id, url, reason, raw_reply FROM evaluation_results "
        f"WHERE keep IS NULL AND run_id IN ({ph}) ORDER BY url", tuple(ids)).fetchall()
    return [dict(r) for r in rows]


def continuable_reason(chain: List[dict], shortlist_total: Optional[int] = None) -> Optional[str]:
    """If the evaluation still has work to do — a phase in progress, a phase that
    stopped below its input (input ≠ kept + dropped because pages remain), or a
    pending exclusion — return a short reason. Otherwise None (nothing to continue).

    'input' is the shortlist total for inclusion, and the previous phase's keeps for
    exclusion. Pure/testable; the web layer supplies shortlist_total.
    """
    by_id = {r["run_id"]: r for r in chain}
    have_excl = any("exclusion" in (r.get("phase") or "").lower() for r in chain)

    for r in chain:                                  # a phase still running
        if not r.get("finished_at"):
            return f"{r.get('phase') or 'A phase'} is still in progress."
    for r in chain:                                  # a finished phase that stopped short
        if "exclusion" in (r.get("phase") or "").lower():
            src = by_id.get(r.get("source_run_id"))
            target = src.get("kept") if src else None
        else:
            target = shortlist_total
        pages = r.get("pages") or 0
        if target is not None and pages < target:
            return (f"{r.get('phase')} evaluated {pages:,} of {target:,} — "
                    f"{target - pages:,} still to do (input ≠ kept + dropped).")
    incl = next((r for r in chain if "inclusion" in (r.get("phase") or "").lower()), None)
    if incl and incl.get("finished_at") and (incl.get("kept") or 0) > 0 and not have_excl:
        return f"Phase 2 (Exclusion) hasn't run over the {incl['kept']:,} kept pages."
    return None


def run_commentary(chain: List[dict], shortlist_total: Optional[int] = None,
                   opened_run_id: Optional[str] = None) -> Dict[str, list]:
    """Plain-English notes about a run's phase chain (oldest-first), grouped into:
      phases    — what has run (and what's pending),
      errors    — unparseable replies / unfinished phases,
      reconcile — whether each phase's input equals kept + dropped (+ unparseable),
      next_steps — what to do next (complete the run, run exclusion, unparseable, …).

    Pure/deterministic so it's unit-testable; the web layer supplies shortlist_total
    (the inclusion phase's input), which run was opened, and renders the result.
    """
    by_id = {r["run_id"]: r for r in chain}
    phases: list = []
    errors: list = []
    reconcile: list = []
    have_excl = any("exclusion" in (r.get("phase") or "").lower() for r in chain)

    for r in chain:
        phase = r.get("phase") or "Phase"
        pages = r.get("pages") or 0
        kept = r.get("kept") or 0
        dropped = r.get("dropped") or 0
        unpar = r.get("unparseable") or 0
        finished = bool(r.get("finished_at"))
        is_excl = "exclusion" in phase.lower()
        if is_excl:
            src = by_id.get(r.get("source_run_id"))
            inp = (src.get("kept") if src else None)
            inp_desc = (f"{inp:,} kept by the previous phase" if inp is not None
                        else "the previous phase's keeps")
        else:
            inp = shortlist_total
            inp_desc = f"{inp:,} shortlist pages" if inp is not None else "the shortlist"

        # (a) what ran
        phases.append({"kind": "ok" if finished else "info",
                       "text": f"{phase}: {'complete' if finished else 'in progress'} — "
                               f"{pages:,} evaluated ({kept:,} kept, {dropped:,} dropped"
                               + (f", {unpar:,} unparseable" if unpar else "") + ")."})
        # (b) errors
        if unpar:
            errors.append({"kind": "warn",
                           "text": f"{phase}: {unpar:,} model repl{'y' if unpar == 1 else 'ies'} could not be "
                                   "parsed (counted as errors — neither kept nor dropped)."})
        if not finished:
            errors.append({"kind": "warn", "text": f"{phase} has not finished — its totals are partial."})

        # (c) reconciliation: input == kept + dropped (+ unparseable)
        if inp is None:
            continue
        settled = kept + dropped + unpar
        if pages == inp and settled == pages and unpar == 0:
            reconcile.append({"kind": "ok",
                              "text": f"{phase}: input {inp:,} = kept {kept:,} + dropped {dropped:,}. ✓"})
        else:
            bits = []
            if pages < inp:
                bits.append(f"{inp - pages:,} of the {inp_desc} not evaluated yet")
            elif pages > inp:
                bits.append(f"evaluated {pages:,}, more than the {inp:,} input")
            if unpar:
                bits.append(f"{unpar:,} unparseable, so kept + dropped is short by {unpar:,}")
            if settled != pages:
                bits.append(f"kept + dropped{' + unparseable' if unpar else ''} ({settled:,}) ≠ pages ({pages:,})")
            reconcile.append({"kind": "warn",
                              "text": f"{phase}: input {inp:,} vs {kept:,} kept + {dropped:,} dropped"
                                      + (f" + {unpar:,} unparseable" if unpar else "")
                                      + " — " + "; ".join(bits) + "."})

    # Pending exclusion phase
    if not have_excl:
        incl = next((r for r in chain if "inclusion" in (r.get("phase") or "").lower()), None)
        if incl:
            ik = incl.get("kept") or 0
            if incl.get("finished_at") and ik > 0:
                phases.append({"kind": "info",
                               "text": f"Phase 2 – Exclusion: not run yet — {ik:,} kept pages are waiting to "
                                       "be re-checked."})
            elif not incl.get("finished_at"):
                phases.append({"kind": "info",
                               "text": "Phase 2 – Exclusion: starts automatically once inclusion finishes "
                                       "(if any pages are kept)."})

    # ---- what to do next -------------------------------------------------
    next_steps: list = []
    opened = by_id.get(opened_run_id)
    total_unpar = sum((r.get("unparseable") or 0) for r in chain)
    incl = next((r for r in chain if "inclusion" in (r.get("phase") or "").lower()), None)

    if opened is not None and not opened.get("finished_at"):
        p = opened.get("pages") or 0
        if "exclusion" in (opened.get("phase") or "").lower():
            src = by_id.get(opened.get("source_run_id"))
            tgt = src.get("kept") if src else None
        else:
            tgt = shortlist_total
        rem = (tgt - p) if (tgt is not None and tgt > p) else None
        next_steps.append({"kind": "info",
                           "text": "Complete this run"
                                   + (f" — {rem:,} of {tgt:,} pages still to evaluate" if rem else "")
                                   + ". Use “Complete this run” above to finish it in the background."})
    if incl and incl.get("finished_at") and (incl.get("kept") or 0) > 0 and not have_excl:
        next_steps.append({"kind": "info",
                           "text": f"Run Phase 2 (Exclusion) over the {incl['kept']:,} kept pages — open the "
                                   "category’s Preview → Semantic Match and Evaluate (it also auto-starts when "
                                   "inclusion completes)."})
    if total_unpar > 0:
        next_steps.append({"kind": "warn",
                           "text": f"{total_unpar:,} page(s) could not be parsed and were left unscored — see "
                                   "“Not parsed” below. They aren’t retried automatically; re-evaluate them by "
                                   "re-running (e.g. a fresh run, or a different model) to score them."})
    if not next_steps:
        next_steps.append({"kind": "ok", "text": "Nothing to do — this evaluation looks complete."})

    return {"phases": phases, "errors": errors, "reconcile": reconcile, "next_steps": next_steps}


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


def run_results_query(run_id: str, keep: Optional[int] = None, limit: Optional[int] = None,
                      source_run_id: Optional[str] = None):
    """(sql, params) for a run's per-page results — so the page can show the SQL it ran. When
    `source_run_id` is given (an exclusion run's inclusion run), also return that run's reason
    for the same page as `src_reason`, so the inclusion and exclusion reasons can be shown side
    by side."""
    if source_run_id:
        p = "r."
        sql = (f"SELECT r.url AS url, r.keep AS keep, r.score AS score, r.reason AS reason, "
               f"s.reason AS src_reason FROM evaluation_results r "
               f"LEFT JOIN evaluation_results s ON s.run_id = {_P} AND s.url = r.url "
               f"WHERE r.run_id = {_P}")
        params: list = [source_run_id, run_id]
    else:
        p = ""
        sql = f"SELECT url, keep, score, reason, NULL AS src_reason FROM evaluation_results WHERE run_id = {_P}"
        params = [run_id]
    if keep is not None:
        sql += f" AND {p}keep = {_P}"
        params.append(keep)
    sql += (f" ORDER BY {p}score DESC NULLS LAST, {p}url" if _IS_PG
            else f" ORDER BY {p}score DESC, {p}url")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return sql, params


def run_results(conn, run_id: str, keep: Optional[int] = None,
                limit: Optional[int] = None, source_run_id: Optional[str] = None) -> List[dict]:
    sql, params = run_results_query(run_id, keep=keep, limit=limit, source_run_id=source_run_id)
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
