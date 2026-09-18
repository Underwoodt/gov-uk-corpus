"""User-editable AI model registry: supplier + model id + full price matrix.

Each model holds the supplier's published pricing grid (USD per 1M tokens):

    input, cache hit   -> off-peak / peak
    input, cache miss  -> off-peak / peak
    output             -> off-peak / peak

The two legacy columns ``input_per_m`` / ``output_per_m`` are kept in sync as the
*standard rate* (cache-miss off-peak input, off-peak output) so the cost engine can
keep reading a single pair without knowing about tiers.

Backend-agnostic. Seeded with a couple of sensible defaults on first use so the
list is never empty.
"""
from __future__ import annotations

import itertools
import time
from typing import Dict, List, Optional

from .backend import db

_id_seq = itertools.count()   # keeps ids unique even for rapid inserts

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

# The six tiered price columns, in display order.
PRICE_FIELDS = [
    "in_hit_off", "in_hit_peak",     # input, cache hit:  off-peak / peak
    "in_miss_off", "in_miss_peak",   # input, cache miss: off-peak / peak
    "out_off", "out_peak",           # output:            off-peak / peak
]

_COLS = "id, provider, model_id, input_per_m, output_per_m, " + ", ".join(PRICE_FIELDS)

# (provider, model_id, input_per_m, output_per_m) seeded when the table is empty.
# Tiered rates default from these (peak = off-peak, cache-hit = cache-miss); edit
# per model on the Settings page to enter the supplier's real grid.
_DEFAULTS = [
    ("anthropic", "claude-haiku-4-5-20251001", 1.0, 5.0),
    ("anthropic", "claude-sonnet-5", 2.0, 10.0),
    ("deepseek", "deepseek-chat", 0.27, 1.10),
]


def normalise_prices(prices: Optional[Dict], input_per_m: float = 0.0,
                     output_per_m: float = 0.0) -> Dict[str, float]:
    """Fill in a full six-value price grid, defaulting missing tiers sensibly.

    Cache-miss off-peak falls back to ``input_per_m``; output off-peak to
    ``output_per_m``; peak falls back to off-peak; cache-hit to cache-miss.
    """
    p = dict(prices or {})

    def val(key, fallback):
        v = p.get(key)
        return float(fallback if v in (None, "") else v)

    d = {}
    d["in_miss_off"] = val("in_miss_off", input_per_m)
    d["in_miss_peak"] = val("in_miss_peak", d["in_miss_off"])
    d["in_hit_off"] = val("in_hit_off", d["in_miss_off"])
    d["in_hit_peak"] = val("in_hit_peak", d["in_hit_off"])
    d["out_off"] = val("out_off", output_per_m)
    d["out_peak"] = val("out_peak", d["out_off"])
    return d


def list_models_query():
    """(sql, params) for the models list — so Settings can show the SQL it ran."""
    return (f"SELECT {_COLS} FROM ai_models ORDER BY provider, model_id", [])


def list_models(conn) -> List[Dict]:
    sql, params = list_models_query()
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def get_model(conn, model_id) -> Optional[Dict]:
    row = conn.execute(f"SELECT * FROM ai_models WHERE id = {_P}", (model_id,)).fetchone()
    return dict(row) if row else None


def find(conn, provider: str, model_id: str) -> Optional[Dict]:
    row = conn.execute(
        f"SELECT * FROM ai_models WHERE provider = {_P} AND model_id = {_P}",
        (provider, model_id)).fetchone()
    return dict(row) if row else None


def add_model(conn, provider: str, model_id: str, input_per_m: float = 0.0,
              output_per_m: float = 0.0, prices: Optional[Dict] = None) -> int:
    mid = int(time.time() * 1000) * 1000 + (next(_id_seq) % 1000)
    g = normalise_prices(prices, input_per_m, output_per_m)
    conn.execute(
        f"INSERT INTO ai_models (id, provider, model_id, input_per_m, output_per_m, "
        f"{', '.join(PRICE_FIELDS)}, created_at) "
        f"VALUES ({', '.join([_P] * (5 + len(PRICE_FIELDS) + 1))})",
        (mid, provider, model_id.strip(), g["in_miss_off"], g["out_off"],
         *[g[f] for f in PRICE_FIELDS], db.now_iso()))
    conn.commit()
    return mid


def update_model(conn, row_id, provider: str, model_id: str,
                 input_per_m: float = 0.0, output_per_m: float = 0.0,
                 prices: Optional[Dict] = None) -> None:
    g = normalise_prices(prices, input_per_m, output_per_m)
    sets = "provider = {p}, model_id = {p}, input_per_m = {p}, output_per_m = {p}, ".format(p=_P)
    sets += ", ".join(f"{f} = {_P}" for f in PRICE_FIELDS)
    conn.execute(
        f"UPDATE ai_models SET {sets} WHERE id = {_P}",
        (provider, model_id.strip(), g["in_miss_off"], g["out_off"],
         *[g[f] for f in PRICE_FIELDS], row_id))
    conn.commit()


def delete_model(conn, model_id) -> None:
    conn.execute(f"DELETE FROM ai_models WHERE id = {_P}", (model_id,))
    conn.commit()


def seed_defaults(conn) -> None:
    """Insert the default models if the table is empty."""
    if conn.execute("SELECT COUNT(*) AS n FROM ai_models").fetchone()["n"]:
        return
    for provider, model_id, i, o in _DEFAULTS:
        add_model(conn, provider, model_id, i, o)
