"""Evaluate one page and persist its verdict — the per-page core shared by the web app's run
driver, its retry-unparsable path and the benchmark CLI.

`evaluate_one_page` builds the prompt (current or cached variant) and calls the model; it
touches NO DB so it can run concurrently across pages. `persist_result` writes the verdict,
the served model, the cost and the usage row in one place (it used to be duplicated).
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from . import evaluate, llm


def evaluate_one_page(cfg, is_exclusion, variant, caching, tmpl, inclusion, exclusion,
                      name, keep_hints, drop_hints, r, *, sampling: Optional[dict] = None,
                      reply_fn: Optional[Callable] = None, chat_fn: Optional[Callable] = None):
    """Returns (res, ms, prompt). `sampling` = {temperature, thinking, effort} or None (provider
    defaults). `reply_fn` / `chat_fn` default to llm.reply / llm.chat; the web app passes its own
    (patchable) hooks. The 'cached' variant sends the stable half as a (cacheable) system prompt
    and the page half as the user message."""
    reply_fn = reply_fn or llm.reply
    chat_fn = chat_fn or llm.chat
    t0 = time.time()
    if is_exclusion:
        prompt = evaluate.build_exclusion_prompt(
            name, inclusion, exclusion, keep_hints, drop_hints,
            r["title"], r["body"], r.get("pass1_reason") or "", template=tmpl,
            pass1_topic=r.get("pass1_topic") or "")
    else:
        prompt = evaluate.build_prompt(inclusion, exclusion, r["title"], r.get("description"),
                                       r["body"], template=tmpl)
    # Sampling controls are passed only when set, so a plain 3-argument reply hook (the mocked
    # tests, older callers) keeps working unchanged.
    extra = {}
    if sampling and any(v is not None for v in sampling.values()):
        extra["sampling"] = sampling
    if variant == "cached":
        stable, variable = evaluate.split_cached(prompt)
        s = sampling or {}
        res = chat_fn(cfg, stable, [{"role": "user", "content": variable}],
                      max_tokens=llm.eval_max_tokens(), cache_system=caching,
                      **({"temperature": s.get("temperature"), "thinking": s.get("thinking"),
                          "effort": s.get("effort")} if extra else {}))
    else:
        res = reply_fn(cfg, "", prompt, **extra)
    return res, int((time.time() - t0) * 1000), prompt


def persist_result(conn, run_id: str, cid: int, r: dict, res: dict, ms: int, *,
                   is_exclusion: bool = False, kind: str = "evaluate"):
    """Parse the reply, store the verdict (with the page's content_hash), the served model, the
    run cost and the ai_usage row. Returns (decision, cost)."""
    decision = (evaluate.parse_exclusion(res.get("reply", "")) if is_exclusion
                else evaluate.parse_decision(res.get("reply", "")))
    evaluate.save_page(conn, run_id, cid, r["url"], decision, ms, raw_reply=res.get("reply"),
                       content_hash=r.get("content_hash"), stop_reason=res.get("stop_reason"))
    evaluate.set_actual_model(conn, run_id, res.get("actual_model"))
    cost = res.get("cost_usd") or 0.0
    evaluate.add_run_cost(conn, run_id, cost, res.get("input_tokens"), res.get("output_tokens"),
                          res.get("cache_hit_tokens"), res.get("cache_miss_tokens"))
    llm.log_usage(conn, cost, res.get("input_tokens"), res.get("output_tokens"), kind)
    return decision, cost
