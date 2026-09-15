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


def _field(usage, *names):
    """Read the first present field from an SDK usage object or a plain dict."""
    d = {}
    if not isinstance(usage, dict):
        for meth in ("model_dump", "to_dict", "dict"):
            fn = getattr(usage, meth, None)
            if callable(fn):
                try:
                    d = fn()
                    break
                except Exception:
                    pass
    else:
        d = usage
    for n in names:
        v = getattr(usage, n, None) if not isinstance(usage, dict) else None
        if v is None:
            v = d.get(n)
        if v is not None:
            return v
    return None


def usage_breakdown(usage) -> Optional[dict]:
    """Normalise a provider usage payload to {in_total, hit, miss, out} token counts.

    Handles both shapes:
      * DeepSeek (OpenAI-style): prompt_tokens, completion_tokens,
        prompt_cache_hit_tokens, prompt_cache_miss_tokens.
      * Anthropic: input_tokens (fresh, excludes cache reads), output_tokens,
        cache_read_input_tokens (hit), cache_creation_input_tokens (billed as miss).
    """
    if usage is None:
        return None
    out = _field(usage, "output_tokens", "completion_tokens")
    hit = _field(usage, "prompt_cache_hit_tokens", "cache_read_input_tokens")
    miss = _field(usage, "prompt_cache_miss_tokens")
    fresh = _field(usage, "input_tokens")                       # Anthropic: excludes cache reads
    creation = _field(usage, "cache_creation_input_tokens")
    total_prompt = _field(usage, "prompt_tokens")

    hit = int(hit or 0)
    if miss is not None:                                        # DeepSeek gives miss directly
        miss = int(miss)
    elif fresh is not None:                                     # Anthropic: fresh + cache-creation
        miss = int(fresh) + int(creation or 0)
    elif total_prompt is not None:
        miss = max(int(total_prompt) - hit, 0)
    else:
        miss = 0
    in_total = int(total_prompt) if total_prompt is not None else miss + hit
    if out is None:
        return None
    return {"in_total": in_total, "hit": hit, "miss": miss, "out": int(out)}


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
