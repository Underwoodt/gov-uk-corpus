"""Peak/off-peak schedule tests.
Run: python3 -m unittest -v tests.test_peak_schedule"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, peak_schedule as ps


class TestPeakSchedule(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_default_all_off_peak(self):
        grid = ps.get_grid(self.conn, "deepseek")
        self.assertEqual(len(grid), 7)
        self.assertEqual(len(grid[0]), 24)
        self.assertTrue(all(v == 0 for row in grid for v in row))
        self.assertEqual(ps.peak_hour_count(self.conn, "deepseek"), 0)

    def test_round_trip_and_is_peak(self):
        grid = [[0] * 24 for _ in range(7)]
        grid[0][9] = 1        # Monday 09:00 UTC peak
        grid[6][23] = 1       # Sunday 23:00 UTC peak
        ps.set_grid(self.conn, "deepseek", grid)

        out = ps.get_grid(self.conn, "deepseek")
        self.assertEqual(out[0][9], 1)
        self.assertEqual(out[6][23], 1)
        self.assertEqual(ps.peak_hour_count(self.conn, "deepseek"), 2)

        # Monday is weekday()==0.
        mon_9 = datetime(2026, 9, 14, 9, 30, tzinfo=timezone.utc)
        mon_10 = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(ps.is_peak(self.conn, "deepseek", mon_9))
        self.assertFalse(ps.is_peak(self.conn, "deepseek", mon_10))

    def test_schedules_are_per_provider(self):
        grid = [[1] * 24 for _ in range(7)]
        ps.set_grid(self.conn, "deepseek", grid)
        self.assertEqual(ps.peak_hour_count(self.conn, "deepseek"), 168)
        self.assertEqual(ps.peak_hour_count(self.conn, "anthropic"), 0)


if __name__ == "__main__":
    unittest.main()
