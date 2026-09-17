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
from govuk_corpus import accounts


class TestPasswordPolicy(unittest.TestCase):
    def test_accepts_min_length(self):
        accounts.validate_password("a" * accounts.MIN_PASSWORD_LEN)   # no raise

    def test_rejects_too_short(self):
        with self.assertRaises(ValueError):
            accounts.validate_password("a" * (accounts.MIN_PASSWORD_LEN - 1))
        with self.assertRaises(ValueError):
            accounts.validate_password("")

    def test_rejects_too_long(self):
        with self.assertRaises(ValueError):
            accounts.validate_password("a" * (accounts.MAX_PASSWORD_LEN + 1))

    def test_allows_spaces_and_symbols(self):
        accounts.validate_password("correct horse battery!")   # spaces + punctuation ok

    def test_rejects_non_ascii_and_control(self):
        with self.assertRaises(ValueError):
            accounts.validate_password("passwordé-with-accent")   # accented letter
        with self.assertRaises(ValueError):
            accounts.validate_password("bad\tcontrol\nchars")     # control chars


class TestSuggestedPassphrase(unittest.TestCase):
    def test_shape(self):
        for _ in range(200):
            p = accounts.suggest_passphrase()
            self.assertGreater(len(p), 12)
            self.assertEqual(p.count("-"), p.count("-"))          # hyphen-joined
            self.assertGreaterEqual(len(p.split("-")), 3)
            accounts.validate_password(p)                         # a suggestion must pass policy

    def test_is_random(self):
        self.assertGreater(len({accounts.suggest_passphrase() for _ in range(50)}), 1)


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
