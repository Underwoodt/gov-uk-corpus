"""User-editable AI model registry: supplier + model id + per-1M-token prices.

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

# (provider, model_id, input_per_m, output_per_m) seeded when the table is empty.
_DEFAULTS = [
    ("anthropic", "claude-haiku-4-5-20251001", 1.0, 5.0),
    ("anthropic", "claude-sonnet-5", 2.0, 10.0),
    ("deepseek", "deepseek-chat", 0.27, 1.10),
]


def list_models(conn) -> List[Dict]:
    rows = conn.execute(
        "SELECT id, provider, model_id, input_per_m, output_per_m FROM ai_models "
        "ORDER BY provider, model_id").fetchall()
    return [dict(r) for r in rows]


def get_model(conn, model_id) -> Optional[Dict]:
    row = conn.execute(f"SELECT * FROM ai_models WHERE id = {_P}", (model_id,)).fetchone()
    return dict(row) if row else None


def find(conn, provider: str, model_id: str) -> Optional[Dict]:
    row = conn.execute(
        f"SELECT * FROM ai_models WHERE provider = {_P} AND model_id = {_P}",
        (provider, model_id)).fetchone()
    return dict(row) if row else None


def add_model(conn, provider: str, model_id: str, input_per_m: float, output_per_m: float) -> int:
    mid = int(time.time() * 1000) * 1000 + (next(_id_seq) % 1000)
    conn.execute(
        f"INSERT INTO ai_models (id, provider, model_id, input_per_m, output_per_m, created_at) "
        f"VALUES ({_P},{_P},{_P},{_P},{_P},{_P})",
        (mid, provider, model_id.strip(), float(input_per_m), float(output_per_m), db.now_iso()))
    conn.commit()
    return mid


def update_model(conn, row_id, provider: str, model_id: str,
                 input_per_m: float, output_per_m: float) -> None:
    conn.execute(
        f"UPDATE ai_models SET provider = {_P}, model_id = {_P}, "
        f"input_per_m = {_P}, output_per_m = {_P} WHERE id = {_P}",
        (provider, model_id.strip(), float(input_per_m), float(output_per_m), row_id))
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
