"""Per-supplier peak / off-peak weekly schedule.

Each supplier (provider) has a 7x24 grid — one cell per hour of the week, Monday
00:00 through Sunday 23:00 — marking that hour as *peak* or *off-peak*. Suppliers
such as DeepSeek bill different token rates by time of day, so cost calculations can
later pick the peak or off-peak price for the hour a call ran.

Stored compactly in ``app_settings`` under ``peak_hours_<provider>`` as a 168-char
string of '0' (off-peak) / '1' (peak), day-major: index = weekday*24 + hour, with
weekday 0 = Monday (matching ``datetime.weekday()``). Hours are UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from . import settings

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOURS = 24
CELLS = len(DAYS) * HOURS   # 168


def _key(provider: str) -> str:
    return f"peak_hours_{provider}"


def get_bitmap(conn, provider: str) -> str:
    """Return the raw 168-char bitmap, defaulting to all off-peak."""
    raw = settings.get_setting(conn, _key(provider), "") or ""
    if len(raw) != CELLS or any(c not in "01" for c in raw):
        return "0" * CELLS
    return raw


def get_grid(conn, provider: str) -> List[List[int]]:
    """Return a 7x24 grid of 0/1 (peak) for the provider."""
    bits = get_bitmap(conn, provider)
    return [[int(bits[d * HOURS + h]) for h in range(HOURS)] for d in range(len(DAYS))]


def set_grid(conn, provider: str, grid: List[List[int]]) -> None:
    """Persist a 7x24 grid (truthy = peak)."""
    bits = "".join("1" if grid[d][h] else "0" for d in range(len(DAYS)) for h in range(HOURS))
    settings.set_setting(conn, _key(provider), bits)


def is_peak(conn, provider: str, when: datetime = None) -> bool:
    """Whether the given moment (UTC; default now) falls in a peak hour for the provider."""
    if when is None:
        when = datetime.now(timezone.utc)
    idx = when.weekday() * HOURS + when.hour
    return get_bitmap(conn, provider)[idx] == "1"


def peak_hour_count(conn, provider: str) -> int:
    return get_bitmap(conn, provider).count("1")
