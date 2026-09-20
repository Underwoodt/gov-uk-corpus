"""Plain-English keyword tokenisation explainer. SQLite has no stemmer, so this checks the
structure/wording of the fallback; the Postgres analyzer path is exercised in production.
Run: python3 -m unittest -v tests.test_keyword_explain"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, keyword_explain


class TestKeywordExplain(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_single_word(self):
        r = keyword_explain.explain_term(self.conn, "Markings")
        self.assertFalse(r["is_phrase"])
        self.assertEqual(r["content_words"], ["Markings"])
        self.assertIn("contains", r["plain"].lower())
        self.assertIn("Markings", r["plain"])

    def test_phrase(self):
        r = keyword_explain.explain_term(self.conn, "Export Health Certificate")
        self.assertTrue(r["is_phrase"])
        self.assertIn("together, in this order", r["plain"])
        self.assertIn("Certificate", r["plain"])

    def test_explain_terms_skips_blanks_and_dedupes(self):
        out = keyword_explain.explain_terms(self.conn, ["slurry", "", "  ", "Slurry", "lagoon"])
        self.assertEqual([t["term"] for t in out], ["slurry", "lagoon"])   # case-insensitive de-dupe


if __name__ == "__main__":
    unittest.main()
