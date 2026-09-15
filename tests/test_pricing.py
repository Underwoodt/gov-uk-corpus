"""Tiered/peak pricing tests.
Run: python3 -m unittest -v tests.test_pricing"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import peak_schedule as ps, pricing

# A DeepSeek-style grid (USD per 1M tokens).
GRID = {"in_hit_off": 0.003, "in_hit_peak": 0.006,
        "in_miss_off": 0.15, "in_miss_peak": 0.30,
        "out_off": 0.60, "out_peak": 1.20}


class TestPricing(unittest.TestCase):
    def test_off_peak_cost(self):
        # 1,000,000 input (all fresh) + 1,000,000 output, off-peak.
        c = pricing.call_cost(GRID, peak=False, in_tokens=1_000_000, out_tokens=1_000_000)
        self.assertAlmostEqual(c, 0.15 + 0.60, places=6)

    def test_peak_cost(self):
        c = pricing.call_cost(GRID, peak=True, in_tokens=1_000_000, out_tokens=1_000_000)
        self.assertAlmostEqual(c, 0.30 + 1.20, places=6)

    def test_cache_hit_tokens_billed_at_hit_rate(self):
        # 800k fresh input, 200k cache-read, off-peak.
        c = pricing.call_cost(GRID, peak=False, in_tokens=800_000,
                              out_tokens=0, cache_read_tokens=200_000)
        self.assertAlmostEqual(c, (800_000 / 1e6) * 0.15 + (200_000 / 1e6) * 0.003, places=6)

    def test_missing_grid_returns_none(self):
        self.assertIsNone(pricing.call_cost({"foo": 1}, peak=False, in_tokens=10, out_tokens=10))

    def test_is_peak_now_uses_bitmap(self):
        bits = ["0"] * ps.CELLS
        bits[0 * 24 + 9] = "1"      # Monday 09:00 UTC peak
        bitmap = "".join(bits)
        mon_9 = datetime(2026, 9, 14, 9, 15, tzinfo=timezone.utc)   # a Monday
        mon_10 = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(pricing.is_peak_now(bitmap, mon_9))
        self.assertFalse(pricing.is_peak_now(bitmap, mon_10))
        self.assertFalse(pricing.is_peak_now("", mon_9))           # no schedule -> off-peak


if __name__ == "__main__":
    unittest.main()
