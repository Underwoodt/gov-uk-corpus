"""Effective per-call cost from a model's tiered price grid + a supplier peak schedule.

A model row (from ``ai_models``) carries six rates: input cache-hit and cache-miss,
each off-peak / peak, plus output off-peak / peak. Which off-peak-or-peak column
applies is decided by the supplier's weekly peak schedule at the moment the call ran
(``peak_schedule``). Input tokens the API served from cache are billed at the
cache-hit rate; the rest at cache-miss. Output is billed at the output rate.

All rates are USD per 1M tokens. Falls back gracefully to a flat input/output rate
when a model has no tiered grid yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import peak_schedule


def is_peak_now(bitmap: Optional[str], when: datetime = None) -> bool:
    """Whether `when` (UTC; default now) is a peak hour, given a 168-char bitmap."""
    if not bitmap or len(bitmap) != peak_schedule.CELLS:
        return False
    if when is None:
        when = datetime.now(timezone.utc)
    return bitmap[when.weekday() * peak_schedule.HOURS + when.hour] == "1"


def _rate(grid: dict, base: str, peak: bool) -> Optional[float]:
    v = grid.get(f"{base}_{'peak' if peak else 'off'}")
    return None if v is None else float(v)


def call_cost(grid: dict, peak: bool, in_tokens: int, out_tokens: int,
              cache_read_tokens: int = 0) -> Optional[float]:
    """Cost of one call under the (peak/off-peak) tier. None if the grid lacks rates.

    ``in_tokens`` is the non-cached input count (as the Anthropic API reports it);
    ``cache_read_tokens`` are billed at the cache-hit rate.
    """
    in_miss = _rate(grid, "in_miss", peak)
    out = _rate(grid, "out", peak)
    if in_miss is None or out is None:
        return None
    in_hit = _rate(grid, "in_hit", peak)
    if in_hit is None:
        in_hit = in_miss
    fresh = max(int(in_tokens or 0), 0)
    cached = max(int(cache_read_tokens or 0), 0)
    cost = (fresh / 1e6) * in_miss + (cached / 1e6) * in_hit + (int(out_tokens or 0) / 1e6) * out
    return round(cost, 6)
