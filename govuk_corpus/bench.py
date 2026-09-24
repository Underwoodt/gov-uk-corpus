"""Pipeline quality benchmark: repeated, pinned runs over the gold set, and the report.

    python3 -m govuk_corpus.bench estimate --category CID [--config haiku|sonnet46|all] [--repeats 5]
    python3 -m govuk_corpus.bench run      --category CID --config haiku --repeats 5 [--concurrency 4]
                                           [--dry-run] [--allow-drift]
    python3 -m govuk_corpus.bench report   --category CID --out docs/bench/2026-09-25/

Design (docs/bench/protocol.md): each Phase-1 model runs `repeats` times over the labelled gold
urls at temperature=0 with thinking off; every Phase-1 run is then re-checked by BOTH Phase-2
models (the crossed 2×2, paired on identical keeps). Runs are ordinary `evaluation_runs` rows
named `bench/p1-<m>/r<k>` and `bench/p1-<m>-p2-<m2>/r<k>`, scoped (their prompt_spec carries the
url list + gold fingerprint) and bench-tagged (the web app never auto-advances them). A crashed
or budget-stopped invocation resumes: an existing run with the same name and gold fingerprint is
continued, never duplicated. The category's active run is never touched.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import ai_models, bench_metrics as bm, evaluate, evaluate_driver, gold, llm
from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

# key -> (provider, model id, $/M input, $/M output). Git-versioned: the arms are part of the protocol.
MODELS: Dict[str, Tuple[str, str, float, float]] = {
    "haiku": ("anthropic", "claude-haiku-4-5-20251001", 1.0, 5.0),
    "sonnet46": ("anthropic", "claude-sonnet-4-6", 3.0, 15.0),
}
SAMPLING = {"temperature": 0.0, "thinking": None, "effort": None}   # thinking omitted = off on both
DEFAULT_REPEATS = 5
DEFAULT_CONCURRENCY = 4
MAX_CONSEC_ERRORS = 6
OUT_TOKENS_P1, OUT_TOKENS_P2 = 220, 120
SONNET_TOKEN_FACTOR = 1.3
DEFAULT_P1_KEEP_RATE = 0.75
CHUNK = 25


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def p1_name(m: str, k: int) -> str:
    return f"bench/p1-{m}/r{k}"


def p2_name(m: str, m2: str, k: int) -> str:
    return f"bench/p1-{m}-p2-{m2}/r{k}"


def configs(config: str) -> List[str]:
    if config == "all":
        return list(MODELS)
    if config not in MODELS:
        raise SystemExit(f"unknown --config {config!r}; choose one of {', '.join(MODELS)} or all")
    return [config]


# ---- setup -----------------------------------------------------------------------

def ensure_models(conn) -> Dict[str, dict]:
    """Make sure every benchmark model has an ai_models row (prices) and accepts sampling."""
    out = {}
    for key, (provider, model_id, pin, pout) in MODELS.items():
        if not llm.supports_sampling(model_id):
            raise SystemExit(f"{model_id} rejects temperature — it cannot be a benchmark arm")
        row = ai_models.find(conn, provider, model_id)
        if not row:
            ai_models.add_model(conn, provider, model_id, pin, pout)
            conn.commit()
            row = ai_models.find(conn, provider, model_id)
        out[key] = row
    return out


def gold_set(conn, cid: int) -> Tuple[List[str], str, Dict[str, dict]]:
    """(sorted labelled urls, gold fingerprint, url -> gold row)."""
    rows = gold.load_gold(conn, cid)
    if not rows:
        raise SystemExit(f"no gold labels for category {cid}: run `gold import` first")
    return sorted(r["url"] for r in rows), gold.gold_sha(rows), {r["url"]: r for r in rows}


def drifted(conn, by_url: Dict[str, dict]) -> List[str]:
    urls = list(by_url)
    out = []
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        marks = ",".join([_P] * len(chunk))
        for r in conn.execute(f"SELECT url, content_hash FROM content WHERE url IN ({marks})",
                              tuple(chunk)).fetchall():
            r = dict(r)
            g = by_url[r["url"]]
            if g.get("content_hash_at_label") and r["content_hash"] \
                    and r["content_hash"] != g["content_hash_at_label"]:
                out.append(r["url"])
    return sorted(out)


def find_run(conn, cid: int, name: str, sha: str) -> Optional[dict]:
    """An existing bench run with this name whose scope was stamped against the same gold."""
    rows = conn.execute(
        f"SELECT run_id FROM evaluation_runs WHERE category_id = {_P} AND name = {_P} "
        f"ORDER BY started_at DESC", (cid, name)).fetchall()
    for r in rows:
        rid = dict(r)["run_id"]
        sc = evaluate.run_scope(conn, rid)
        if sc and sc.get("sha") == sha:
            return evaluate.get_run(conn, rid)
    return None


def _spec_inputs(spec: dict):
    return (spec.get("template"), spec.get("inclusion_context", ""), spec.get("exclusion_context", ""),
            spec.get("name") or "the topic", spec.get("keep_hints", ""), spec.get("drop_hints", ""))


def _cfg(conn, key: str) -> dict:
    provider, model_id, _, _ = MODELS[key]
    cfg = llm.cfg_for(conn, provider, model_id)
    return cfg


# ---- the dry-run model ---------------------------------------------------------------

def fake_reply(cfg: dict, system: str, prompt: str, **kw) -> dict:
    """A deterministic stand-in for the model: keep iff the page text contains a word of the
    INCLUDE line (>4 letters). Costs nothing; produces well-formed JSON for both phases."""
    body = prompt.lower()
    incl = ""
    for line in prompt.splitlines():
        if line.strip() and not line.startswith(("You ", "Topic", "=", "Page", "Return", "{", "-", "Rules",
                                                  "Evidence", "Primary", "Score", "keep", "Apply", "KEEP",
                                                  "DROP", "If ", "For ", "The ", "Use ", "Inclusion", "Exclusion")):
            incl = line.strip()
            break
    words = [w for w in incl.lower().split() if len(w) > 4][:3]
    # Only the page text: after the "Page content" line, before the template's own Rules / Return.
    page = body.split("page content", 1)[-1]
    page = page.split("\n\nrules\n", 1)[0].split("\n\nreturn only", 1)[0]
    hit = next((w for w in words if w in page), None)
    if "exclusion_hit" in prompt:
        reply = json.dumps({"keep": hit is not None, "exclusion_hit": "none" if hit else "wrong_domain",
                            "reason": "dry-run"})
    else:
        reply = json.dumps({"keep": hit is not None, "score": 0.8 if hit else 0.0,
                            "where": ["body"] if hit else [], "evidence": [hit] if hit else [],
                            "primary_topic": "dry-run topic", "reason": "dry-run"})
    return {"reply": reply, "model": cfg.get("model"), "actual_model": cfg.get("model") + "-dry",
            "provider": cfg.get("provider"), "stop_reason": "end_turn", "input_tokens": len(prompt) // 4,
            "output_tokens": 40, "cost_usd": 0.0, "cache_hit_tokens": 0, "cache_miss_tokens": 0}


# ---- driving one run ---------------------------------------------------------------------

def _drive(conn, run: dict, cid: int, cfg: dict, is_exclusion: bool, candidates: Callable[[int], List[dict]],
           concurrency: int, reply_fn: Optional[Callable], log: Callable[[str], None]) -> str:
    """Evaluate every candidate page of a run in waves; persist each verdict. Returns
    'complete' | 'budget' | 'errors'."""
    run_id = run["run_id"]
    spec = evaluate.run_prompt_spec(conn, run_id) or {}
    tmpl, inclusion, exclusion, name, keep_hints, drop_hints = _spec_inputs(spec)
    sampling = evaluate.run_trial(conn, run_id)["sampling"]
    budget, spent = llm.budget(conn), llm.daily_spend(conn)
    evaluate.mark_run_running(conn, run_id, pid=os.getpid())
    consec = 0
    done = 0
    while True:
        if budget > 0 and spent >= budget:
            evaluate.mark_run_stopped(conn, run_id)
            log(f"  budget reached (${spent:.2f} of ${budget:.2f}) — {run['name']} paused after {done} pages")
            return "budget"
        rows = candidates(CHUNK)
        if not rows:
            break
        wave = rows[:concurrency]

        def one(r):
            return (r,) + evaluate_driver.evaluate_one_page(
                cfg, is_exclusion, "current", False, tmpl, inclusion, exclusion, name,
                keep_hints, drop_hints, r, sampling=sampling, reply_fn=reply_fn)
        if concurrency > 1 and len(wave) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(wave)) as ex:
                packed = list(ex.map(one, wave))
        else:
            packed = [one(r) for r in wave]
        for r, res, ms, _prompt in packed:
            if res.get("error"):
                if res.get("fatal"):
                    evaluate.mark_run_stopped(conn, run_id)
                    raise SystemExit(f"fatal: {res['error']}")
                consec += 1
                if consec >= MAX_CONSEC_ERRORS:
                    evaluate.mark_run_stopped(conn, run_id)
                    log(f"  stopped after {consec} errors in a row (last: {res['error']})")
                    return "errors"
                evaluate.save_page(conn, run_id, cid, r["url"],
                                   {"keep": None, "score": None,
                                    "reason": f"skipped after AI error: {str(res['error'])[:300]}"}, ms,
                                   raw_reply=res.get("error"), content_hash=r.get("content_hash"))
                continue
            consec = 0
            _d, c = evaluate_driver.persist_result(conn, run_id, cid, r, res, ms,
                                                   is_exclusion=is_exclusion, kind="bench")
            spent += c
            done += 1
    evaluate.finish_run(conn, run_id)
    return "complete"


def run(conn, cid: int, config: str, repeats: int = DEFAULT_REPEATS, concurrency: int = DEFAULT_CONCURRENCY,
        dry_run: bool = False, allow_drift: bool = False, reply_fn: Optional[Callable] = None,
        log: Callable[[str], None] = print) -> dict:
    """Run (or resume) the benchmark for one Phase-1 config (or all), with both Phase-2 models
    off each Phase-1 run. Returns a summary of run ids per arm."""
    ensure_models(conn)
    urls, sha, by_url = gold_set(conn, cid)
    dr = drifted(conn, by_url)
    if dr and not allow_drift:
        raise SystemExit(f"{len(dr)} gold pages changed since labelling (re-label or --allow-drift):\n  "
                         + "\n  ".join(dr))
    if dry_run:
        reply_fn = reply_fn or fake_reply
    scope = {"kind": "gold", "urls": urls, "sha": sha, "n": len(urls)}
    summary: Dict[str, List[str]] = {}
    status = "complete"
    for m in configs(config):
        cfg = _cfg(conn, m)
        if not cfg.get("key") and not dry_run:
            raise SystemExit(f"no API key for {cfg['label']}")
        for k in range(1, repeats + 1):
            name = p1_name(m, k)
            p1 = find_run(conn, cid, name, sha)
            if not p1:
                spec = evaluate.prompt_spec_json(conn, cid, evaluate.PHASE_INCLUSION,
                                                 {"concurrency": concurrency, "sampling": SAMPLING},
                                                 scope=scope, bench={"arm": f"p1-{m}", "p1": m, "repeat": k,
                                                                     "dry_run": dry_run})
                rid = evaluate.create_run(conn, cid, cfg["model"], cfg["provider"],
                                          phase=evaluate.PHASE_INCLUSION, name=name, prompt_spec=spec)
                p1 = evaluate.get_run(conn, rid)
                log(f"{name}: created {rid}")
            else:
                log(f"{name}: resuming {p1['run_id']} ({p1.get('pages') or 0} pages done)")
            summary.setdefault(name, []).append(p1["run_id"])
            if not p1.get("finished_at"):
                st = _drive(conn, p1, cid, cfg, False,
                            lambda lim, rid=p1["run_id"]: evaluate.scoped_candidates(conn, rid, urls, lim),
                            concurrency, reply_fn, log)
                if st != "complete":
                    return {"status": st, "runs": summary}
                p1 = evaluate.get_run(conn, p1["run_id"])
            log(f"{name}: complete — {p1['pages']} pages, kept {p1['kept']}, ${p1.get('cost') or 0:.4f}")
            for m2 in MODELS:
                cfg2 = _cfg(conn, m2)
                name2 = p2_name(m, m2, k)
                p2 = find_run(conn, cid, name2, sha)
                if not p2:
                    spec2 = evaluate.prompt_spec_json(conn, cid, evaluate.PHASE_EXCLUSION,
                                                      {"concurrency": concurrency, "sampling": SAMPLING},
                                                      scope=scope, bench={"arm": f"p1-{m}-p2-{m2}", "p1": m,
                                                                          "p2": m2, "repeat": k,
                                                                          "dry_run": dry_run})
                    rid2 = evaluate.create_run(conn, cid, cfg2["model"], cfg2["provider"],
                                               phase=evaluate.PHASE_EXCLUSION, name=name2,
                                               source_run_id=p1["run_id"], prompt_spec=spec2)
                    p2 = evaluate.get_run(conn, rid2)
                    log(f"{name2}: created {rid2}")
                summary.setdefault(name2, []).append(p2["run_id"])
                if not p2.get("finished_at"):
                    st = _drive(conn, p2, cid, cfg2, True,
                                lambda lim, rid=p2["run_id"], src=p1["run_id"]:
                                evaluate.exclusion_candidates(conn, rid, src, lim),
                                concurrency, reply_fn, log)
                    if st != "complete":
                        return {"status": st, "runs": summary}
                    p2 = evaluate.get_run(conn, p2["run_id"])
                log(f"{name2}: complete — {p2['pages']} pages, kept {p2['kept']}, ${p2.get('cost') or 0:.4f}")
    return {"status": status, "runs": summary}


# ---- estimate ----------------------------------------------------------------------------

def estimate(conn, cid: int, config: str = "all", repeats: int = DEFAULT_REPEATS) -> dict:
    """Cost estimate from the real prompts: Σ len(rendered prompt)/4 per page (×1.3 for Sonnet),
    Phase 2 on the latest run's Phase-1 keep rate, priced from ai_models."""
    models = ensure_models(conn)
    try:
        urls, sha, by_url = gold_set(conn, cid)
    except SystemExit:
        category = __import__("govuk_corpus.categories", fromlist=["get_category"]).get_category(conn, cid)
        urls = [p["url"] for p in gold.forwarded_pages(conn, category)]
        sha = None
    spec1 = json.loads(evaluate.prompt_spec_json(conn, cid, evaluate.PHASE_INCLUSION))
    spec2 = json.loads(evaluate.prompt_spec_json(conn, cid, evaluate.PHASE_EXCLUSION))
    t1, inc, exc, name, kh, dh = _spec_inputs(spec1)
    t2 = spec2["template"]
    rows = evaluate.scoped_candidates(conn, "bench-estimate", urls, len(urls) + 1)
    p1_chars = sum(len(evaluate.build_prompt(inc, exc, r["title"], r.get("description"), r["body"], template=t1))
                   for r in rows)
    p2_chars = sum(len(evaluate.build_exclusion_prompt(name, inc, exc, kh, dh, r["title"], r["body"],
                                                       "first pass reason", template=t2, pass1_topic="topic"))
                   for r in rows)
    # Phase-2 volume: the latest inclusion run's keep rate, else the protocol default.
    keep_rate = DEFAULT_P1_KEEP_RATE
    inc_run = evaluate.latest_inclusion_run(conn, cid)
    if inc_run:
        rr = evaluate.get_run(conn, inc_run)
        if rr and (rr.get("pages") or 0) > 0:
            keep_rate = (rr.get("kept") or 0) / rr["pages"]
    out = {"pages": len(rows), "gold_sha": sha, "repeats": repeats, "p1_keep_rate": round(keep_rate, 3),
           "budget": llm.budget(conn), "spent_today": llm.daily_spend(conn), "arms": {}}
    total = 0.0
    for m in configs(config):
        row = models[m]
        factor = SONNET_TOKEN_FACTOR if "sonnet" in m else 1.0
        in1 = p1_chars / 4 * factor
        out1 = OUT_TOKENS_P1 * len(rows)
        c1 = in1 / 1e6 * row["input_per_m"] + out1 / 1e6 * row["output_per_m"]
        arm = {"p1_per_run": round(c1, 4), "p2_per_run": {}}
        arm_total = c1 * repeats
        for m2 in MODELS:
            r2 = models[m2]
            f2 = SONNET_TOKEN_FACTOR if "sonnet" in m2 else 1.0
            in2 = p2_chars / 4 * f2 * keep_rate
            out2 = OUT_TOKENS_P2 * len(rows) * keep_rate
            c2 = in2 / 1e6 * r2["input_per_m"] + out2 / 1e6 * r2["output_per_m"]
            arm["p2_per_run"][m2] = round(c2, 4)
            arm_total += c2 * repeats
        arm["total_for_repeats"] = round(arm_total, 2)
        out["arms"][m] = arm
        total += arm_total
    out["total"] = round(total, 2)
    out["fits_budget"] = out["budget"] <= 0 or (out["spent_today"] + total) <= out["budget"]
    return out


# ---- report ------------------------------------------------------------------------------

def _bench_runs(conn, cid: int) -> List[dict]:
    rows = conn.execute(
        f"SELECT * FROM evaluation_runs WHERE category_id = {_P} AND name LIKE 'bench/%' "
        f"ORDER BY started_at", (cid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["spec"] = json.loads(d.get("prompt_spec") or "{}")
        except ValueError:
            d["spec"] = {}
        out.append(d)
    return out


def _results(conn, run_id: str) -> Dict[str, dict]:
    rows = conn.execute(
        f"SELECT url, keep, score, reason, where_hit, evidence, primary_topic, content_hash, stop_reason, ms "
        f"FROM evaluation_results WHERE run_id = {_P}", (run_id,)).fetchall()
    return {dict(r)["url"]: dict(r) for r in rows}


def _pages_sent(conn, urls: Sequence[str], body_limit: int) -> Dict[str, dict]:
    out = {}
    urls = list(urls)
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        marks = ",".join([_P] * len(chunk))
        for r in conn.execute(f"SELECT url, title, description, search_text, content_hash FROM content "
                              f"WHERE url IN ({marks})", tuple(chunk)).fetchall():
            r = dict(r)
            out[r["url"]] = {"title": r["title"], "description": r["description"],
                             "body": evaluate.truncate_body(r["search_text"] or "", body_limit),
                             "content_hash": r["content_hash"]}
    return out


def _fmt(v, pct=False, nd=3) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if pct:
        return f"{100 * v:.1f}%"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def report(conn, cid: int, out_dir: str, log: Callable[[str], None] = print) -> dict:
    """Compute every metric in the protocol from the bench runs and write the report bundle."""
    os.makedirs(out_dir, exist_ok=True)
    urls, sha, by_url = gold_set(conn, cid)
    gold_labels = {u: by_url[u]["label"] for u in urls}
    weights = {u: gold.weight_of(by_url[u]) for u in urls}
    weighted_any = any(w != 1.0 for w in weights.values())
    runs = [r for r in _bench_runs(conn, cid) if (r["spec"].get("scope") or {}).get("sha") == sha
            and r["spec"].get("bench")]
    other = [r["name"] for r in _bench_runs(conn, cid) if (r["spec"].get("scope") or {}).get("sha") != sha]
    if not runs:
        raise SystemExit(f"no bench runs stamped against gold {sha} (other gold versions: {len(other)})")
    from .prompts import template_version
    expected_hash = {evaluate.PHASE_INCLUSION: evaluate.template_fingerprint(evaluate.DEFAULT_INCLUSION_TEMPLATE),
                     evaluate.PHASE_EXCLUSION: evaluate.template_fingerprint(evaluate.DEFAULT_EXCLUSION_TEMPLATE)}
    hash_mismatch = [r["name"] for r in runs if r["spec"].get("template_hash") != expected_hash.get(r["phase"])]
    body_limit = int(runs[0]["spec"].get("body_limit") or evaluate.BODY_CHAR_LIMIT)
    pages = _pages_sent(conn, urls, body_limit)
    results = {r["run_id"]: _results(conn, r["run_id"]) for r in runs}
    # Drift: evaluated content_hash != content_hash_at_label -> excluded from accuracy metrics.
    drift = set()
    for rid, res in results.items():
        for u, row in res.items():
            g = by_url.get(u)
            if g and row.get("content_hash") and g.get("content_hash_at_label") \
                    and row["content_hash"] != g["content_hash_at_label"]:
                drift.add(u)
    gold_ok = {u: l for u, l in gold_labels.items() if u not in drift}
    incl = [r for r in runs if r["phase"] == evaluate.PHASE_INCLUSION]
    excl = [r for r in runs if r["phase"] == evaluate.PHASE_EXCLUSION]
    by_p1: Dict[str, List[dict]] = {}
    for r in sorted(incl, key=lambda r: r["spec"]["bench"].get("repeat", 0)):
        by_p1.setdefault(r["spec"]["bench"]["p1"], []).append(r)
    p2_of: Dict[str, Dict[str, dict]] = {}
    for r in excl:
        p2_of.setdefault(r["source_run_id"], {})[r["spec"]["bench"]["p2"]] = r

    def verdicts(rid):
        return {u: results[rid].get(u, {}).get("keep") for u in urls}

    def scores(rid):
        return {u: results[rid].get(u, {}).get("score") for u in urls}

    per_run_rows, stability_rows, grounding_rows, paired_rows, per_page_rows = [], [], [], [], []
    ag = gold.agreement_stats(conn, cid)
    summary: Dict = {"gold_sha": sha, "n": len(urls), "labels": {}, "drifted": sorted(drift),
                     "template_hash_mismatch": hash_mismatch, "arms": {}, "p1": {},
                     "agreement": {k: v for k, v in ag.items() if k != "disagreements"},
                     "disagreements": len(ag["disagreements"]),
                     "adjudicated": sum(1 for u in urls if by_url[u].get("adjudicated_at")),
                     "weighted": weighted_any,
                     "sampling": sorted({(by_url[u].get("sample_stage") or "", round(float(by_url[u]["sample_frac"]), 3))
                                         for u in urls if by_url[u].get("sample_frac")})}
    for l in gold.LABELS:
        summary["labels"][l] = sum(1 for v in gold_labels.values() if v == l)
    include_text = runs[0]["spec"].get("inclusion_context", "")
    p1_majority: Dict[str, Dict[str, Optional[int]]] = {}
    for m, rs in by_p1.items():
        reps = [verdicts(r["run_id"]) for r in rs]
        scs = [scores(r["run_id"]) for r in rs]
        arm: Dict = {"repeats": len(rs), "runs": [r["run_id"] for r in rs], "phase1": {}, "e2e": {}}
        for bound in bm.BOUNDS:
            ms = [bm.phase_metrics(gold_ok, v, bound) for v in reps]
            arm["phase1"][bound] = {
                "micro": bm.rates(bm.confusion(
                    [(bm.resolve_gold(gold_ok[u], bound), v.get(u)) for v in reps for u in gold_ok])),
                # Inverse-probability weighted (pages sampled per stage on guc-0029 count 1/fraction).
                "micro_weighted": bm.rates(bm.confusion_weighted(
                    [(bm.resolve_gold(gold_ok[u], bound), v.get(u), weights[u]) for v in reps for u in gold_ok])),
                "per_repeat": ms,
                "majority": bm.phase_metrics(gold_ok, bm.majority_vote(reps, urls), bound),
                "majority_weighted": bm.phase_metrics_weighted(gold_ok, bm.majority_vote(reps, urls), weights, bound),
            }
        arm["borderline_kept_share"] = [bm.borderline_agreement(gold_labels, v) for v in reps]
        st = bm.stability(reps, urls)
        arm["stability"] = {k: st[k] for k in ("repeats", "pages", "unanimous", "unanimous_share",
                                                "flip_share", "fleiss_kappa", "flip_pages")}
        arm["score_stability"] = bm.score_stability(scs, urls, band_fn=gold.score_band)
        arm["calibration"] = {b: bm.calibration(gold_ok, scs[0], gold.score_band, b) for b in bm.BOUNDS} if scs else {}
        p1_majority[m] = bm.majority_vote(reps, urls)
        # grounding over every P1 row
        checks = []
        for r in rs:
            for u, row in results[r["run_id"]].items():
                pg = pages.get(u, {})
                try:
                    evidence = json.loads(row.get("evidence") or "[]")
                except ValueError:
                    evidence = []
                try:
                    where = json.loads(row.get("where_hit") or "[]")
                except ValueError:
                    where = []
                ch = bm.grounding_checks({"keep": row.get("keep"), "score": row.get("score"), "evidence": evidence,
                                          "where_hit": where, "primary_topic": row.get("primary_topic"),
                                          "parsed": row.get("keep") is not None},
                                         pg, include_text=include_text,
                                         max_tokens_hit=(row.get("stop_reason") == "max_tokens"))
                checks.append(ch)
                grounding_rows.append({"arm": f"p1-{m}", "run": r["name"], "url": u, **ch})
        arm["grounding"] = bm.pass_rates(checks)
        arm["unparseable_rate"] = (sum(1 for r in rs for row in results[r["run_id"]].values()
                                       if row.get("keep") is None)
                                   / max(1, sum(len(results[r["run_id"]]) for r in rs)))
        # cost / latency
        ms_all = [row.get("ms") for r in rs for row in results[r["run_id"]].values() if row.get("ms") is not None]
        cost_per_run = [r.get("cost") or 0.0 for r in rs]
        arm["cost"] = {"per_run_mean": sum(cost_per_run) / len(cost_per_run), "per_run": cost_per_run,
                       "ms_median": bm.percentile(ms_all, 0.5), "ms_p90": bm.percentile(ms_all, 0.9),
                       **bm.cost_efficiency(sum(cost_per_run) / len(cost_per_run),
                                            arm["phase1"]["D"]["micro"])}
        for r, v in zip(rs, reps):
            d = bm.phase_metrics(gold_ok, v, "D")
            per_run_rows.append({"arm": f"p1-{m}", "phase": "P1", "run": r["name"], "run_id": r["run_id"],
                                 "pages": r.get("pages"), "kept": r.get("kept"), "cost": r.get("cost"),
                                 **{k: d[k] for k in ("precision", "recall", "f1", "specificity", "tp", "fp", "fn", "tn", "unparseable")}})
        # Phase 2 arms (crossed): pair each P1 run with each P2 model's run on it
        for m2 in MODELS:
            e_reps, va_reps, p2_runs = [], [], []
            for r in rs:
                p2 = (p2_of.get(r["run_id"]) or {}).get(m2)
                if not p2:
                    continue
                p2_runs.append(p2)
                v1, v2 = verdicts(r["run_id"]), {u: results[p2["run_id"]].get(u, {}).get("keep")
                                                  for u in results[p2["run_id"]]}
                e = bm.end_to_end(v1, v2)
                e_reps.append(e)
                va_reps.append({b: bm.phase2_value_add(gold_ok, v1, v2, b) for b in bm.BOUNDS})
                d = bm.phase_metrics(gold_ok, e, "D")
                per_run_rows.append({"arm": f"p1-{m}-p2-{m2}", "phase": "E2E", "run": p2["name"],
                                     "run_id": p2["run_id"], "pages": p2.get("pages"), "kept": p2.get("kept"),
                                     "cost": p2.get("cost"),
                                     **{k: d[k] for k in ("precision", "recall", "f1", "specificity", "tp", "fp", "fn", "tn", "unparseable")}})
            if not e_reps:
                continue
            e2e: Dict = {"repeats": len(e_reps), "runs": [p["run_id"] for p in p2_runs]}
            for bound in bm.BOUNDS:
                e2e[bound] = {"micro": bm.rates(bm.confusion(
                    [(bm.resolve_gold(gold_ok[u], bound), e.get(u)) for e in e_reps for u in gold_ok])),
                    "micro_weighted": bm.rates(bm.confusion_weighted(
                        [(bm.resolve_gold(gold_ok[u], bound), e.get(u), weights[u]) for e in e_reps for u in gold_ok])),
                    "value_add_mean": {k: sum(v[bound][k] or 0 for v in va_reps) / len(va_reps)
                                       for k in ("fp_removed", "tp_wrongly_dropped", "net")},
                    "value_add": [v[bound] for v in va_reps]}
            st2 = bm.stability(e_reps, urls)
            e2e["stability"] = {k: st2[k] for k in ("unanimous_share", "flip_share", "fleiss_kappa")}
            e2e["cost_per_run_mean"] = sum(p.get("cost") or 0.0 for p in p2_runs) / len(p2_runs)
            arm["e2e"][m2] = e2e
        summary["arms"][m] = arm
        for u in urls:
            per_page_rows.append({"arm": f"p1-{m}", "url": u, "gold": gold_labels[u], "drifted": u in drift,
                                  "keeps": st["per_page"][u]["keeps"], "repeats": len(rs),
                                  "majority": p1_majority[m].get(u),
                                  "scores": ";".join("" if s.get(u) is None else str(s[u]) for s in scs)})
        for u in st["flip_pages"]:
            stability_rows.append({"arm": f"p1-{m}", "url": u, "gold": gold_labels[u],
                                   "keeps": st["per_page"][u]["keeps"], "repeats": len(rs)})
    # Paired comparison between the two Phase-1 models (majority vote), H1
    if len(p1_majority) == 2:
        a, b = list(p1_majority)
        for bound in bm.BOUNDS:
            ab, ba = bm.discordant(gold_ok, p1_majority[b], p1_majority[a], bound)   # b right/a wrong, ...
            rec = bm.bootstrap_ci(gold_ok, p1_majority[b], p1_majority[a], metric="recall", bound=bound)
            f1 = bm.bootstrap_ci(gold_ok, p1_majority[b], p1_majority[a], metric="f1", bound=bound)
            ra, rb = bm.phase_metrics(gold_ok, p1_majority[a], bound), bm.phase_metrics(gold_ok, p1_majority[b], bound)
            row = {"bound": bound, "a": a, "b": b, "recall_a": ra["recall"], "recall_b": rb["recall"],
                   "recall_diff_b_minus_a": rec["point"], "recall_ci_lo": rec["lo"], "recall_ci_hi": rec["hi"],
                   "f1_diff": f1["point"], "f1_ci_lo": f1["lo"], "f1_ci_hi": f1["hi"],
                   "discordant_b_right": ab, "discordant_a_right": ba, "mcnemar_p": bm.mcnemar_exact(ab, ba),
                   "cohens_h_recall": bm.cohens_h(rb["recall"], ra["recall"]),
                   "cohens_kappa_models": bm.cohens_kappa(p1_majority[a], p1_majority[b], urls)}
            paired_rows.append(row)
        summary["paired"] = paired_rows
        d = paired_rows[0]
        summary["H1"] = (d["recall_diff_b_minus_a"] is not None and d["recall_ci_lo"] is not None
                         and d["recall_diff_b_minus_a"] >= 0.05 and d["recall_ci_lo"] > 0)
    # ---- write the bundle ----
    def write_csv(name, rows):
        if not rows:
            return
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in keys})
    write_csv("per_page.csv", per_page_rows)
    write_csv("per_run.csv", per_run_rows)
    write_csv("stability.csv", stability_rows)
    write_csv("grounding.csv", grounding_rows)
    write_csv("paired.csv", paired_rows)
    with open(os.path.join(out_dir, "runs.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: v for k, v in r.items() if k != "spec"} | {"prompt_spec": r["spec"]} for r in runs],
                  fh, indent=1, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, default=str)
    md = _results_md(summary, template_version(), expected_hash)
    with open(os.path.join(out_dir, "results.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    log(f"wrote {out_dir}/results.md (+ per_page, per_run, stability, grounding, paired CSVs, runs.json)")
    return summary


def _results_md(s: dict, code_sha: str, expected_hash: dict) -> str:
    L = []
    lbl = s["labels"]
    L.append(f"# Benchmark results — {now_iso()[:10]}\n")
    L.append(f"Protocol: `docs/bench/protocol.md` · Code: `{code_sha}` · Templates: P1 "
             f"`{expected_hash.get(evaluate.PHASE_INCLUSION)}` / P2 `{expected_hash.get(evaluate.PHASE_EXCLUSION)}` · "
             f"Gold: `{s['gold_sha']}` (n = {s['n']}: in {lbl.get('in', 0)} / out {lbl.get('out', 0)} / "
             f"borderline {lbl.get('borderline', 0)}; drifted excluded {len(s['drifted'])})\n")
    if s["template_hash_mismatch"]:
        L.append(f"**WARNING** template_hash mismatch on: {', '.join(s['template_hash_mismatch'])}\n")
    if lbl.get("out", 0) < 15:
        L.append(f"_Note: only {lbl.get('out', 0)} definite `out` pages — precision and specificity are "
                 f"descriptive (protocol §5)._\n")
    ag = s.get("agreement") or {}
    if ag.get("labellers"):
        pairs = "; ".join(f"{p['a']} vs {p['b']}: {p['shared']} shared, agree {_fmt(p['agree'], pct=True)}, κ {_fmt(p['kappa'])}"
                          for p in ag.get("pairs", []))
        L.append(f"_Labellers: {len(ag['labellers'])} ({', '.join(ag['labellers'])}); pages with 2+ votes "
                 f"{ag.get('pages_multi', 0)}, unanimous {ag.get('unanimous', 0)}, disagreements {s.get('disagreements', 0)}, "
                 f"adjudicated {s.get('adjudicated', 0)}, blind votes {ag.get('blind_votes', 0)}; Fleiss' κ "
                 f"{_fmt(ag.get('fleiss_kappa'))}{'; ' + pairs if pairs else ''}._\n")
    if s.get("weighted"):
        L.append("_Gold pages were sampled per pipeline stage (guc-0029): stage / fraction = "
                 + ", ".join(f"{st} {f:.0%}" for st, f in s["sampling"])
                 + ". **Weighted** figures count each label 1/fraction (inverse-probability); raw figures "
                 "treat the sample as the population and overstate recall when drops were under-sampled._\n")
    L.append("## Headline — Phase-1 recall on D (micro over repeats, T=0)\n")
    L.append("| Arm | Repeats | Recall (D) | Precision (D) | F1 | Specificity | Unparseable | Unanimous | Fleiss κ | $/run | ms p50 / p90 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for m, a in s["arms"].items():
        d = a["phase1"]["D"]["micro"]
        st = a["stability"]
        c = a["cost"]
        L.append(f"| p1-{m} | {a['repeats']} | {_fmt(d['recall'])} | {_fmt(d['precision'])} | {_fmt(d['f1'])} | "
                 f"{_fmt(d['specificity'])} | {_fmt(a['unparseable_rate'], pct=True)} | "
                 f"{_fmt(st['unanimous_share'], pct=True)} | {_fmt(st['fleiss_kappa'])} | "
                 f"{_fmt(c['per_run_mean'], nd=4)} | {_fmt(c['ms_median'], nd=0)} / {_fmt(c['ms_p90'], nd=0)} |")
        if s.get("weighted"):
            w = a["phase1"]["D"]["micro_weighted"]
            L.append(f"| p1-{m} (weighted) | {a['repeats']} | {_fmt(w['recall'])} | {_fmt(w['precision'])} | {_fmt(w['f1'])} | "
                     f"{_fmt(w['specificity'])} | | | | | |")
    if s.get("paired"):
        d = s["paired"][0]
        L.append(f"\n**H1** ({d['b']} − {d['a']} Phase-1 recall on D, majority vote): Δ = {_fmt(d['recall_diff_b_minus_a'])} "
                 f"[95% CI {_fmt(d['recall_ci_lo'])}, {_fmt(d['recall_ci_hi'])}] → **{'held' if s.get('H1') else 'not held'}**. "
                 f"McNemar exact p = {_fmt(d['mcnemar_p'], nd=4)} on {d['discordant_b_right'] + d['discordant_a_right']} discordant pages; "
                 f"Cohen's h = {_fmt(d['cohens_h_recall'])}; κ between models = {_fmt(d['cohens_kappa_models'])}.\n")
    L.append("## Bounds (borderline → in gives the recall upper bound; → out the precision upper bound)\n")
    L.append("| Arm | Metric | D | borderline→in | borderline→out |")
    L.append("|---|---|---|---|---|")
    for m, a in s["arms"].items():
        for met in ("recall", "precision", "f1"):
            L.append(f"| p1-{m} | {met} | " + " | ".join(_fmt(a['phase1'][b]['micro'][met]) for b in bm.BOUNDS) + " |")
    L.append("\n## Phase 2 (crossed, each Phase-2 model on the same Phase-1 keeps)\n")
    L.append("| P1 → P2 | Repeats | FP removed (mean) | TP wrongly dropped (mean) | Net | E2E recall (D) | E2E precision (D) | E2E F1 | E2E unanimous | $/P2 run |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for m, a in s["arms"].items():
        for m2, e in a["e2e"].items():
            va = e["D"]["value_add_mean"]
            d = e["D"]["micro"]
            L.append(f"| {m} → {m2} | {e['repeats']} | {_fmt(va['fp_removed'], nd=1)} | {_fmt(va['tp_wrongly_dropped'], nd=1)} | "
                     f"{_fmt(va['net'], nd=1)} | {_fmt(d['recall'])} | {_fmt(d['precision'])} | {_fmt(d['f1'])} | "
                     f"{_fmt(e['stability']['unanimous_share'], pct=True)} | {_fmt(e['cost_per_run_mean'], nd=4)} |")
    L.append("\n## Stability (per arm, Phase 1)\n")
    L.append("| Arm | Unanimous | Flip pages | Fleiss κ | Score SD mean | SD > 0.15 | Band stable |")
    L.append("|---|---|---|---|---|---|---|")
    for m, a in s["arms"].items():
        st, ss = a["stability"], a["score_stability"]
        L.append(f"| p1-{m} | {_fmt(st['unanimous_share'], pct=True)} | {len(st['flip_pages'])} | {_fmt(st['fleiss_kappa'])} | "
                 f"{_fmt(ss['score_sd_mean'])} | {_fmt(ss['score_sd_over_threshold_share'], pct=True)} | "
                 f"{_fmt(ss['band_stable_share'], pct=True)} |")
    L.append("\n## Calibration (repeat 1 scores; share of gold-in per band, D)\n")
    L.append("| Arm | Band | n | gold-in share | →in bound | →out bound |")
    L.append("|---|---|---|---|---|---|")
    for m, a in s["arms"].items():
        cal = a["calibration"]
        if not cal:
            continue
        for band in ("0", "0.1-0.3", "0.4-0.6", "0.7-1.0", "unscored"):
            if band in cal["D"]["bands"]:
                L.append(f"| p1-{m} | {band} | {cal['D']['bands'][band]['n']} | {_fmt(cal['D']['bands'][band]['gold_in_share'])} | "
                         f"{_fmt(cal['borderline_in']['bands'].get(band, {}).get('gold_in_share'))} | "
                         f"{_fmt(cal['borderline_out']['bands'].get(band, {}).get('gold_in_share'))} |")
        L.append(f"| p1-{m} | monotone: {_fmt(cal['D']['monotone'])}; Brier {_fmt(cal['D']['brier'])} | | | | |")
    L.append("\n## Grounding pass rates (Phase 1, all repeats)\n")
    checks = ["G1_parsed", "G2_keep_matches_score", "G3_evidence_verbatim", "G4_where_valid",
              "G5_topic_sane", "G6_score_in_range", "G7_not_truncated"]
    L.append("| Check | " + " | ".join(f"p1-{m}" for m in s["arms"]) + " |")
    L.append("|---|" + "---|" * len(s["arms"]))
    for c in checks:
        L.append(f"| {c} | " + " | ".join(_fmt(a['grounding'].get(c), pct=True) for a in s["arms"].values()) + " |")
    L.append("\n## Cost & latency\n")
    L.append("| Arm | $/run mean | $ per correct decision | $ per gold-in recalled | ms p50 | ms p90 |")
    L.append("|---|---|---|---|---|---|")
    for m, a in s["arms"].items():
        c = a["cost"]
        L.append(f"| p1-{m} | {_fmt(c['per_run_mean'], nd=4)} | {_fmt(c['cost_per_correct_decision'], nd=5)} | "
                 f"{_fmt(c['cost_per_gold_in_recalled'], nd=5)} | {_fmt(c['ms_median'], nd=0)} | {_fmt(c['ms_p90'], nd=0)} |")
    if s["drifted"]:
        L.append("\n## Excluded (content changed since labelling)\n")
        L.extend(f"- {u}" for u in s["drifted"])
    L.append("\n## Decisions\n\n- Phase-1 model: _fill in per protocol §7_\n- Phase-2 model: _fill in_\n"
             "- Pin temperature in production: _fill in_\n\n## Deviations from protocol\n\n_none_\n")
    return "\n".join(L) + "\n"


# ---- CLI --------------------------------------------------------------------------------------

def _connect():
    conn = db.connect(os.getenv("CORPUS_DB", "content.db"))
    db.init_db(conn)
    return conn


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m govuk_corpus.bench", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    es = sub.add_parser("estimate")
    es.add_argument("--category", type=int, required=True)
    es.add_argument("--config", default="all")
    es.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    rn = sub.add_parser("run")
    rn.add_argument("--category", type=int, required=True)
    rn.add_argument("--config", required=True, help="haiku | sonnet46 | all")
    rn.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    rn.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    rn.add_argument("--dry-run", action="store_true", help="fake model, no spend")
    rn.add_argument("--allow-drift", action="store_true")
    rp = sub.add_parser("report")
    rp.add_argument("--category", type=int, required=True)
    rp.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    conn = _connect()
    try:
        if a.cmd == "estimate":
            e = estimate(conn, a.category, a.config, a.repeats)
            print(json.dumps(e, indent=2))
            print(f"\nTOTAL ≈ ${e['total']:.2f} for {e['repeats']} repeats over {e['pages']} pages "
                  f"(budget ${e['budget']:.2f}, spent today ${e['spent_today']:.2f}) — "
                  f"{'fits' if e['fits_budget'] else 'DOES NOT FIT'}")
            return 0
        if a.cmd == "run":
            res = run(conn, a.category, a.config, a.repeats, max(1, min(a.concurrency, 10)),
                      dry_run=a.dry_run, allow_drift=a.allow_drift)
            print(json.dumps(res, indent=2))
            return 0 if res["status"] == "complete" else 2
        if a.cmd == "report":
            report(conn, a.category, a.out)
            return 0
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
