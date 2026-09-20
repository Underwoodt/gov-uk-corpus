"""Modelled sustainability impact of the AI evaluation runs.

Providers don't meter per-request energy, so we ESTIMATE it: keep the raw measurements we
already store per run (tokens in/out, cached vs fresh, cost, pages, kept), convert them to
energy → water → CO₂ via a small, editable table of factors, and present the result as a range,
not a false-precision point. The factors are contested and move fast, so they live here as
plain constants (edit + bump FACTOR_VERSION) rather than being baked into the formulas.

Formula per run:
    kWh = ((fresh_in·E_in + cached_in·E_in·cache) + out·E_out) · PUE · (1+embodied) / 3.6e6
    water_L = kWh · L_per_kWh
    CO2_kg  = kWh · grid_kg_per_kWh
"""
from __future__ import annotations

from typing import Dict, Optional

from .backend import db

_P = "%s" if db.__name__.endswith("db_pg") else "?"

# Version the factor set so older figures stay comparable / explainable. Bump on any change.
FACTOR_VERSION = "2026-09-20.1"

# Each factor: (point, low, high). Illustrative, adjustable — see the notes shown in the UI.
FACTORS = {
    "e_in_j_per_tok":  (0.30, 0.10, 1.00),   # energy per INPUT token (J)
    "e_out_j_per_tok": (1.50, 0.50, 5.00),   # energy per OUTPUT token (J) — output costs more
    "cache_factor":    (0.25, 0.10, 0.50),   # cached input tokens use a fraction of the compute
    "pue":             (1.20, 1.10, 1.50),   # data-centre overhead (cooling, networking, idle)
    "water_l_per_kwh": (1.00, 0.30, 2.00),   # litres of water per kWh (region-dependent)
    "grid_kg_per_kwh": (0.20, 0.05, 0.40),   # grid carbon intensity kg CO₂e/kWh (region-dependent)
    "embodied_uplift": (0.00, 0.00, 0.30),   # optional hardware-manufacturing uplift (off by default)
}

FACTOR_NOTES = {
    "e_in_j_per_tok":  "Vendor disclosures + academic estimates vary widely (Google's 2025 per-prompt figure ≈ 0.24 Wh).",
    "e_out_j_per_tok": "Output tokens are roughly 3–10× the energy of input tokens.",
    "cache_factor":    "Prompt caching avoids recomputing the prompt — cached input is cheaper in cost and energy.",
    "pue":             "Power Usage Effectiveness — the data centre's non-compute overhead.",
    "water_l_per_kwh": "On-site cooling plus indirect (electricity) water; heavily region-dependent.",
    "grid_kg_per_kwh": "Grid intensity: UK ≈ 0.20, US avg ≈ 0.40, some regions < 0.05. Use the provider region if known.",
    "embodied_uplift": "Hardware manufacturing footprint; often omitted. Off by default — flag if included.",
}


def _pt(name: str) -> float:
    return FACTORS[name][0]


def _impact_at(intok, outtok, hit, miss, idx: int) -> Dict[str, float]:
    """kWh / water / CO₂ using the point (idx 0), low (1) or high (2) value of every factor."""
    intok, outtok, hit, miss = int(intok or 0), int(outtok or 0), int(hit or 0), int(miss or 0)
    fresh, cached = miss, hit
    if fresh + cached == 0 and intok:        # no cache split recorded -> treat all input as fresh
        fresh = intok
    f = lambda k: FACTORS[k][idx]
    j_in = fresh * f("e_in_j_per_tok") + cached * f("e_in_j_per_tok") * f("cache_factor")
    j_out = outtok * f("e_out_j_per_tok")
    kwh = (j_in + j_out) * f("pue") * (1 + f("embodied_uplift")) / 3.6e6
    return {"kwh": kwh, "water_l": kwh * f("water_l_per_kwh"), "co2_kg": kwh * f("grid_kg_per_kwh")}


def impact(intok, outtok, hit, miss) -> Dict[str, Dict[str, float]]:
    """{'point':…, 'low':…, 'high':…} each with kwh / water_l / co2_kg."""
    return {"point": _impact_at(intok, outtok, hit, miss, 0),
            "low": _impact_at(intok, outtok, hit, miss, 1),
            "high": _impact_at(intok, outtok, hit, miss, 2)}


def equivalences(kwh: float, water_l: float, co2_kg: float) -> list:
    """Everyday anchors for a kWh / water / CO₂ figure (nobody intuits '0.4 Wh').

    Each anchor is tagged with the ``metric`` it belongs beside (energy / water / co2) and an
    ``icon`` (emoji) so the UI can show it next to the matching headline card."""
    wh = kwh * 1000.0
    ml = water_l * 1000.0
    g = co2_kg * 1000.0
    out = [
        ("energy", "📱", "phone charges", wh / 10.0, "a full charge ≈ 10 Wh"),
        ("energy", "🫖", "kettles boiled", wh / 100.0, "boiling a kettle ≈ 100 Wh"),
        ("energy", "🔍", "web searches", wh / 0.3, "a web search ≈ 0.3 Wh"),
        ("water", "🥤", "cups of water", ml / 250.0, "a cup ≈ 250 mL"),
        ("co2", "🚗", "km driven", g / 120.0, "a petrol car ≈ 120 g CO₂e/km"),
    ]
    return [{"metric": m, "icon": ic, "label": l, "value": v, "note": n}
            for m, ic, l, v, n in out]


def _agg_row(conn, where: str = "", params: tuple = ()) -> dict:
    row = conn.execute(
        f"SELECT COUNT(*) AS runs, COALESCE(SUM(cost),0) AS cost, "
        f"COALESCE(SUM(in_tokens),0) AS intok, COALESCE(SUM(out_tokens),0) AS outtok, "
        f"COALESCE(SUM(hit_tokens),0) AS hit, COALESCE(SUM(miss_tokens),0) AS miss, "
        f"COALESCE(SUM(pages),0) AS pages, COALESCE(SUM(kept),0) AS kept "
        f"FROM evaluation_runs {where}", params).fetchone()
    d = dict(row)
    for k in ("cost", "intok", "outtok", "hit", "miss", "pages", "kept"):
        d[k] = float(d.get(k) or 0)
    d["impact"] = impact(d["intok"], d["outtok"], d["hit"], d["miss"])
    return d


def summary(conn) -> dict:
    """Whole-application sustainability summary from evaluation_runs: totals + modelled impact
    (with range), per-model and per-phase breakdowns, and per-page / per-included-page
    derived figures."""
    total = _agg_row(conn)

    def _grouped(select_cols: str, group_by: str) -> list:
        rows = []
        for r in conn.execute(
                f"SELECT {select_cols}, COUNT(*) AS runs, COALESCE(SUM(cost),0) AS cost, "
                f"COALESCE(SUM(in_tokens),0) AS intok, COALESCE(SUM(out_tokens),0) AS outtok, "
                f"COALESCE(SUM(hit_tokens),0) AS hit, COALESCE(SUM(miss_tokens),0) AS miss, "
                f"COALESCE(SUM(pages),0) AS pages, COALESCE(SUM(kept),0) AS kept "
                f"FROM evaluation_runs GROUP BY {group_by} ORDER BY SUM(cost) DESC").fetchall():
            d = dict(r)
            for k in ("cost", "intok", "outtok", "hit", "miss", "pages", "kept"):
                d[k] = float(d.get(k) or 0)
            d["impact"] = impact(d["intok"], d["outtok"], d["hit"], d["miss"])
            rows.append(d)
        return rows

    by_model = _grouped("COALESCE(provider,'?') AS provider, COALESCE(model,'?') AS model",
                        "provider, model")
    by_phase = _grouped("COALESCE(phase,'—') AS phase", "phase")
    pages, kept = total["pages"] or 0, total["kept"] or 0
    derived = {
        "cost_per_page": (total["cost"] / pages) if pages else None,
        "cost_per_included": (total["cost"] / kept) if kept else None,
        "kwh_per_page": (total["impact"]["point"]["kwh"] / pages) if pages else None,
        "co2_g_per_page": (total["impact"]["point"]["co2_kg"] * 1000 / pages) if pages else None,
        "cached_share": (total["hit"] / total["intok"]) if total["intok"] else None,
        "tokens_per_page": ((total["intok"] + total["outtok"]) / pages) if pages else None,
    }
    return {"total": total, "by_model": by_model, "by_phase": by_phase, "derived": derived,
            "equivalences": equivalences(total["impact"]["point"]["kwh"],
                                         total["impact"]["point"]["water_l"],
                                         total["impact"]["point"]["co2_kg"]),
            "factor_version": FACTOR_VERSION,
            "factors": [{"key": k, "point": v[0], "low": v[1], "high": v[2],
                         "note": FACTOR_NOTES.get(k, "")} for k, v in FACTORS.items()]}
