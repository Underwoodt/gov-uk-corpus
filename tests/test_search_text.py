"""search_text backfill tests (SQLite).
Run: python3 -m unittest -v tests.test_search_text"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus.build_search_text import build, rows_needing_search_text


class TestBuildSearchText(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        payload = json.dumps({"details": {"body": "<p>Store slurry safely</p>"}})
        self.conn.execute("INSERT INTO content (url, content) VALUES (?,?)",
                          ("https://www.gov.uk/a", payload))
        self.conn.execute("INSERT INTO content (url, content, search_text) VALUES (?,?,?)",
                          ("https://www.gov.uk/b", payload, "already done"))   # skipped
        self.conn.execute("INSERT INTO content (url, content) VALUES (?,?)",
                          ("https://www.gov.uk/nojson", "not json"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_rows_needing_only_empty_search_text(self):
        urls = [r["url"] for r in rows_needing_search_text(self.conn)]
        self.assertIn("https://www.gov.uk/a", urls)
        self.assertNotIn("https://www.gov.uk/b", urls)   # already has search_text

    def test_build_populates_body_text(self):
        run_id = db.start_run(self.conn, "search_text", "test")
        counters = build(self.conn, run_id)
        self.assertEqual(counters["written"], 1)     # /a
        self.assertEqual(counters["bad_json"], 1)    # /nojson
        got = self.conn.execute(
            "SELECT search_text FROM content WHERE url=?", ("https://www.gov.uk/a",)).fetchone()
        self.assertEqual(got["search_text"], "Store slurry safely")


if __name__ == "__main__":
    unittest.main()
