"""Pure metric functions for the pipeline quality benchmark (no DB, no numpy).

Definitions follow docs/bench/protocol.md §5. Everything here takes plain Python values so it
is unit-testable and the report is reproducible from the CSVs alone.

Vocabulary: a *verdict* is 1 (keep), 0 (drop) or None (unparseable). Gold labels are
'in' | 'out' | 'borderline'. The definite set D excludes borderline; the two *bounds* map
borderline to in (recall upper bound) or to out (precision upper bound).
"""
from __future__ import annotations

import math
import random
import re
import unicodedata
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

IN, OUT, BORDERLINE = "in", "out", "borderline"
BOUNDS = ("D", "borderline_in", "borderline_out")


# ---- labels ----------------------------------------------------------------------

def resolve_gold(label: str, bound: str = "D") -> Optional[bool]:
    """True = gold in, False = gold out, None = excluded from this bound."""
    if label == IN:
        return True
    if label == OUT:
        return False
    if label == BORDERLINE:
        return {"D": None, "borderline_in": True, "borderline_out": False}[bound]
    return None


# ---- confusion --------------------------------------------------------------------

def confusion(pairs: Iterable[Tuple[Optional[bool], Optional[int]]]) -> Dict[str, int]:
    """pairs of (gold: True/False/None, verdict: 1/0/None). None gold = skipped; None verdict
    counts as a drop (a missed page is missed) and is tallied in `unparseable`."""
    c = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "n": 0, "unparseable": 0}
    for gold, v in pairs:
        if gold is None:
            continue
        c["n"] += 1
        if v is None:
            c["unparseable"] += 1
        keep = v == 1
        if gold and keep:
            c["tp"] += 1
        elif gold and not keep:
            c["fn"] += 1
        elif not gold and keep:
            c["fp"] += 1
        else:
            c["tn"] += 1
    return c


def _div(a: float, b: float) -> Optional[float]:
    return (a / b) if b else None


def rates(c: Dict[str, int]) -> Dict[str, Optional[float]]:
    p = _div(c["tp"], c["tp"] + c["fp"])
    r = _div(c["tp"], c["tp"] + c["fn"])
    f1 = _div(2 * c["tp"], 2 * c["tp"] + c["fp"] + c["fn"])
    spec = _div(c["tn"], c["tn"] + c["fp"])
    acc = _div(c["tp"] + c["tn"], c["n"])
    return {"precision": p, "recall": r, "f1": f1, "specificity": spec, "accuracy": acc,
            **{k: c[k] for k in ("tp", "fp", "fn", "tn", "n", "unparseable")}}


def phase_metrics(gold: Dict[str, str], verdicts: Dict[str, Optional[int]],
                  bound: str = "D") -> Dict[str, Optional[float]]:
    """Precision/recall/F1/... of a verdict map over a gold map, for one bound."""
    pairs = [(resolve_gold(lbl, bound), verdicts.get(u)) for u, lbl in gold.items()]
    return rates(confusion(pairs))


def borderline_agreement(gold: Dict[str, str], verdicts: Dict[str, Optional[int]]) -> Optional[float]:
    """Share of borderline pages the model kept (descriptive)."""
    b = [u for u, l in gold.items() if l == BORDERLINE]
    return _div(sum(1 for u in b if verdicts.get(u) == 1), len(b))


# ---- end-to-end + Phase-2 value-add ---------------------------------------------------

def end_to_end(p1: Dict[str, Optional[int]], p2: Optional[Dict[str, Optional[int]]]) -> Dict[str, Optional[int]]:
    """e = 1 iff p1 = 1 and p2 = 1. Without a Phase-2 run e = p1. A Phase-1 keep that Phase 2
    never reached (not in p2) is treated as kept (Phase 2 only ever turns keeps into drops)."""
    out: Dict[str, Optional[int]] = {}
    for u, v in p1.items():
        if v != 1:
            out[u] = v
        elif p2 is None or u not in p2:
            out[u] = 1
        else:
            out[u] = 1 if p2[u] == 1 else 0
    return out


def phase2_value_add(gold: Dict[str, str], p1: Dict[str, Optional[int]],
                     p2: Dict[str, Optional[int]], bound: str = "D") -> Dict[str, Optional[float]]:
    """Over the Phase-1 keeps: FP removed (good drops), TP wrongly dropped (bad drops), net,
    drop precision = good / (good + bad)."""
    good = bad = keeps = drops = 0
    gold_in_total = 0
    for u, lbl in gold.items():
        g = resolve_gold(lbl, bound)
        if g is None:
            continue
        if g:
            gold_in_total += 1
        if p1.get(u) != 1:
            continue
        keeps += 1
        if p2.get(u) == 0:
            drops += 1
            if g:
                bad += 1
            else:
                good += 1
    return {"p1_keeps": keeps, "p2_drops": drops, "fp_removed": good, "tp_wrongly_dropped": bad,
            "net": good - bad, "drop_precision": _div(good, good + bad),
            "tp_dropped_share_of_gold_in": _div(bad, gold_in_total)}


# ---- stability across repeats ---------------------------------------------------------

def stability(repeats: Sequence[Dict[str, Optional[int]]], urls: Sequence[str]) -> Dict:
    """Per-page agreement across k repeats of the same arm. Returns unanimous share, flip pages
    (0 < keeps < k), Fleiss' kappa over (pages × k raters × 2 categories), and per-page counts."""
    k = len(repeats)
    per_page = {}
    for u in urls:
        vs = [r.get(u) for r in repeats]
        keeps = sum(1 for v in vs if v == 1)
        per_page[u] = {"keeps": keeps, "drops": k - keeps, "unparseable": sum(1 for v in vs if v is None)}
    n = len(per_page)
    unanimous = sum(1 for d in per_page.values() if d["keeps"] in (0, k))
    flips = [u for u, d in per_page.items() if 0 < d["keeps"] < k]
    return {"repeats": k, "pages": n, "unanimous": unanimous, "unanimous_share": _div(unanimous, n),
            "flip_pages": sorted(flips), "flip_share": _div(len(flips), n),
            "fleiss_kappa": fleiss_kappa([(d["keeps"], d["drops"]) for d in per_page.values()]),
            "per_page": per_page}


def fleiss_kappa(counts: Sequence[Tuple[int, ...]]) -> Optional[float]:
    """Fleiss' κ for subjects × categories rating counts (each row sums to the number of raters).
    Returns 1.0 for perfect agreement, None when undefined (no subjects / one rater)."""
    rows = [tuple(c) for c in counts if sum(c) > 0]
    if not rows:
        return None
    n = sum(rows[0])
    if n < 2 or any(sum(r) != n for r in rows):
        return None
    N = len(rows)
    cats = len(rows[0])
    p_j = [sum(r[j] for r in rows) / (N * n) for j in range(cats)]
    P_i = [(sum(x * x for x in r) - n) / (n * (n - 1)) for r in rows]
    P_bar = sum(P_i) / N
    P_e = sum(p * p for p in p_j)
    if P_e >= 1.0:
        return 1.0
    return (P_bar - P_e) / (1 - P_e)


def majority_vote(repeats: Sequence[Dict[str, Optional[int]]], urls: Sequence[str]) -> Dict[str, Optional[int]]:
    """≥ half of the repeats kept → 1 (ties to keep, so an even split is a keep); all None → None."""
    out: Dict[str, Optional[int]] = {}
    for u in urls:
        vs = [r.get(u) for r in repeats]
        if all(v is None for v in vs):
            out[u] = None
            continue
        keeps = sum(1 for v in vs if v == 1)
        out[u] = 1 if keeps * 2 >= len(vs) else 0
    return out


def score_stability(scores: Sequence[Dict[str, Optional[float]]], urls: Sequence[str],
                    band_fn=None, sd_threshold: float = 0.15) -> Dict:
    """Mean per-page score SD across repeats, share of pages with SD > threshold, and share whose
    confidence band (band_fn) is the same in every repeat."""
    sds, bands_stable = [], 0
    n = 0
    for u in urls:
        vs = [s.get(u) for s in scores]
        vs = [float(v) for v in vs if v is not None]
        if len(vs) < 2:
            continue
        n += 1
        m = sum(vs) / len(vs)
        sds.append(math.sqrt(sum((v - m) ** 2 for v in vs) / (len(vs) - 1)))
        if band_fn and len({band_fn(v) for v in vs}) == 1:
            bands_stable += 1
    return {"pages": n, "score_sd_mean": _div(sum(sds), len(sds)),
            "score_sd_over_threshold_share": _div(sum(1 for s in sds if s > sd_threshold), len(sds)),
            "band_stable_share": _div(bands_stable, n) if band_fn else None}


# ---- calibration --------------------------------------------------------------------------

def calibration(gold: Dict[str, str], scores: Dict[str, Optional[float]], band_fn,
                bound: str = "D") -> Dict:
    """Per score band: n and share of gold-in; monotone flag; Brier score (score vs gold-in)."""
    bands: Dict[str, List[bool]] = {}
    brier, m = 0.0, 0
    for u, lbl in gold.items():
        g = resolve_gold(lbl, bound)
        if g is None:
            continue
        s = scores.get(u)
        band = band_fn(s)
        bands.setdefault(band, []).append(g)
        if s is not None:
            brier += (float(s) - (1.0 if g else 0.0)) ** 2
            m += 1
    order = [b for b in ("0", "0.1-0.3", "0.4-0.6", "0.7-1.0") if b in bands]
    table = {b: {"n": len(v), "gold_in_share": _div(sum(v), len(v))} for b, v in bands.items()}
    shares = [table[b]["gold_in_share"] for b in order if table[b]["gold_in_share"] is not None]
    monotone = all(a <= b for a, b in zip(shares, shares[1:])) if len(shares) > 1 else None
    return {"bands": table, "monotone": monotone, "brier": _div(brier, m)}


# ---- grounding checks G1–G7 ----------------------------------------------------------------

_WS = re.compile(r"\s+")
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
           " ": " "}


def norm_text(s: Optional[str]) -> str:
    """NFKC, curly→straight quotes, whitespace collapsed, lower-cased — for substring checks."""
    t = unicodedata.normalize("NFKC", s or "")
    for a, b in _QUOTES.items():
        t = t.replace(a, b)
    return _WS.sub(" ", t).strip().lower()


_TOKEN = re.compile(r"[a-z0-9]+")


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(_TOKEN.findall(norm_text(a))), set(_TOKEN.findall(norm_text(b)))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def grounding_checks(row: Dict, page: Dict, include_text: str = "",
                     max_tokens_hit: bool = False) -> Dict[str, Optional[bool]]:
    """Deterministic checks on one Phase-1 result row against the text the model was sent.
    row: keep, score, evidence (list), where_hit (list), primary_topic, parsed (bool).
    page: title, description, body (already truncated to what was sent).
    None = not applicable (e.g. G3 on a drop)."""
    parsed = bool(row.get("parsed", row.get("keep") is not None))
    keep = row.get("keep")
    score = row.get("score")
    out: Dict[str, Optional[bool]] = {"G1_parsed": parsed}
    if not parsed:
        for k in ("G2_keep_matches_score", "G3_evidence_verbatim", "G4_where_valid",
                  "G5_topic_sane", "G6_score_in_range"):
            out[k] = None
        out["G7_not_truncated"] = not max_tokens_hit
        return out
    try:
        s = float(score) if score is not None else None
    except (TypeError, ValueError):
        s = None
    out["G6_score_in_range"] = s is not None and 0.0 <= s <= 1.0
    out["G2_keep_matches_score"] = (s is not None) and ((keep == 1) == (s > 0))
    fields = {"title": norm_text(page.get("title")), "description": norm_text(page.get("description")),
              "body": norm_text(page.get("body"))}
    evidence = [e for e in (row.get("evidence") or []) if isinstance(e, str) and e.strip()]
    where = [w for w in (row.get("where_hit") or []) if isinstance(w, str)]
    if keep == 1:
        out["G3_evidence_verbatim"] = bool(evidence) and all(
            any(norm_text(e) in txt for txt in fields.values() if txt) for e in evidence)
        valid_names = all(w in fields for w in where)
        each_has_quote = all(any(norm_text(e) in fields[w] for e in evidence) for w in where if w in fields)
        out["G4_where_valid"] = bool(where) and valid_names and each_has_quote
    else:
        out["G3_evidence_verbatim"] = None
        out["G4_where_valid"] = None
    topic = (row.get("primary_topic") or "").strip()
    first_sentence = re.split(r"(?<=[.!?])\s", (include_text or "").strip(), maxsplit=1)[0]
    out["G5_topic_sane"] = bool(topic) and len(topic.split()) <= 10 and (
        _jaccard(topic, first_sentence) < 0.6 if first_sentence else True)
    out["G7_not_truncated"] = not max_tokens_hit
    return out


def pass_rates(check_rows: Sequence[Dict[str, Optional[bool]]]) -> Dict[str, Optional[float]]:
    """Share passing per check, over the rows where the check applied."""
    keys = sorted({k for r in check_rows for k in r})
    out = {}
    for k in keys:
        vals = [r[k] for r in check_rows if r.get(k) is not None]
        out[k] = _div(sum(1 for v in vals if v), len(vals))
    return out


# ---- paired comparison -----------------------------------------------------------------------

def mcnemar_exact(b: int, c: int) -> Optional[float]:
    """Two-sided exact McNemar p-value on the discordant counts b (A right, B wrong) and
    c (A wrong, B right): Binomial(b + c, ½)."""
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def discordant(gold: Dict[str, str], a: Dict[str, Optional[int]], b: Dict[str, Optional[int]],
               bound: str = "D") -> Tuple[int, int]:
    """(A correct & B wrong, A wrong & B correct) over the bound's pages."""
    ab = ba = 0
    for u, lbl in gold.items():
        g = resolve_gold(lbl, bound)
        if g is None:
            continue
        ca = (a.get(u) == 1) == g
        cb = (b.get(u) == 1) == g
        if ca and not cb:
            ab += 1
        elif cb and not ca:
            ba += 1
    return ab, ba


def cohens_kappa(a: Dict[str, Optional[int]], b: Dict[str, Optional[int]], urls: Sequence[str]) -> Optional[float]:
    n = 0
    agree = 0
    ca = Counter()
    cb = Counter()
    for u in urls:
        va, vb = 1 if a.get(u) == 1 else 0, 1 if b.get(u) == 1 else 0
        n += 1
        agree += va == vb
        ca[va] += 1
        cb[vb] += 1
    if n == 0:
        return None
    po = agree / n
    pe = sum((ca[k] / n) * (cb[k] / n) for k in (0, 1))
    return 1.0 if pe >= 1.0 else (po - pe) / (1 - pe)


def cohens_h(p1: Optional[float], p2: Optional[float]) -> Optional[float]:
    if p1 is None or p2 is None:
        return None
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))


def bootstrap_ci(gold: Dict[str, str], verdicts_a: Dict[str, Optional[int]],
                 verdicts_b: Optional[Dict[str, Optional[int]]] = None, metric: str = "recall",
                 bound: str = "D", B: int = 2000, seed: int = 20260924) -> Dict[str, Optional[float]]:
    """Percentile 95 % CI of a metric (or of A − B when verdicts_b is given), resampling pages
    with replacement. Deterministic for a seed."""
    urls = [u for u, l in gold.items() if resolve_gold(l, bound) is not None]
    if not urls:
        return {"point": None, "lo": None, "hi": None, "B": B, "seed": seed}
    rng = random.Random(seed)

    def stat(sample: Sequence[str]) -> Optional[float]:
        g = {u: gold[u] for u in sample}
        # duplicates matter: build pair lists rather than dicts
        pa = [(resolve_gold(gold[u], bound), verdicts_a.get(u)) for u in sample]
        ma = rates(confusion(pa))[metric]
        if verdicts_b is None:
            return ma
        pb = [(resolve_gold(gold[u], bound), verdicts_b.get(u)) for u in sample]
        mb = rates(confusion(pb))[metric]
        return None if ma is None or mb is None else ma - mb

    point = stat(urls)
    draws = []
    for _ in range(B):
        sample = [urls[rng.randrange(len(urls))] for _ in urls]
        v = stat(sample)
        if v is not None:
            draws.append(v)
    if not draws:
        return {"point": point, "lo": None, "hi": None, "B": B, "seed": seed}
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return {"point": point, "lo": lo, "hi": hi, "B": B, "seed": seed, "draws": len(draws)}


# ---- cost ----------------------------------------------------------------------------------

def percentile(values: Sequence[float], q: float) -> Optional[float]:
    vs = sorted(v for v in values if v is not None)
    if not vs:
        return None
    idx = (len(vs) - 1) * q
    lo, hi = int(math.floor(idx)), int(math.ceil(idx))
    return vs[lo] if lo == hi else vs[lo] + (vs[hi] - vs[lo]) * (idx - lo)


def cost_efficiency(cost: float, r: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    correct = (r.get("tp") or 0) + (r.get("tn") or 0)
    return {"cost": cost, "cost_per_correct_decision": _div(cost, correct),
            "cost_per_gold_in_recalled": _div(cost, r.get("tp") or 0)}
