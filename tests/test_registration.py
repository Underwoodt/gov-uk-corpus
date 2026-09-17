"""Registration helper tests (rate limiter). The /register route flow itself is
Postgres-only and is exercised end-to-end against the cloud test DB.
Run: python3 -m unittest -v tests.test_registration"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CORPUS_DB", ":memory:")

from webapp import app as webapp_app


class TestRateLimit(unittest.TestCase):
    def setUp(self):
        webapp_app._RATE.clear()

    def test_blocks_after_max(self):
        key = "register:1.2.3.4"
        for _ in range(5):
            self.assertTrue(webapp_app._rate_limit(key, 5, 3600))
        self.assertFalse(webapp_app._rate_limit(key, 5, 3600))   # 6th is blocked

    def test_keys_are_independent(self):
        self.assertTrue(webapp_app._rate_limit("a", 1, 3600))
        self.assertFalse(webapp_app._rate_limit("a", 1, 3600))
        self.assertTrue(webapp_app._rate_limit("b", 1, 3600))    # different key unaffected

    def test_hits_expire_out_of_window(self):
        # window 0 → any earlier hit is already outside it, so never blocks
        self.assertTrue(webapp_app._rate_limit("w", 1, 0))
        self.assertTrue(webapp_app._rate_limit("w", 1, 0))


if __name__ == "__main__":
    unittest.main()
