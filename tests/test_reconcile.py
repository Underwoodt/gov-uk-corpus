"""Reconcile: URL selection (stalest first, bodies only). No network.
Run: python3 -m unittest -v tests.test_reconcile"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus.reconcile import urls_to_reverify


class TestUrlsToReverify(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            # url, content_hash, last_seen_at
            ("https://www.gov.uk/old", "h", "2026-01-01T00:00:00+00:00"),
            ("https://www.gov.uk/mid", "h", "2026-06-01T00:00:00+00:00"),
            ("https://www.gov.uk/new", "h", "2026-09-01T00:00:00+00:00"),
            ("https://www.gov.uk/unfetched", None, "2026-05-01T00:00:00+00:00"),  # no body -> skip
        ]
        for url, h, seen in rows:
            self.conn.execute(
                "INSERT INTO content (url, content_hash, last_seen_at) VALUES (?,?,?)", (url, h, seen))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_stalest_first_and_bodies_only(self):
        urls = urls_to_reverify(self.conn)
        self.assertEqual(urls, [
            "https://www.gov.uk/old", "https://www.gov.uk/mid", "https://www.gov.uk/new",
        ])  # unfetched excluded; oldest last_seen first

    def test_limit(self):
        self.assertEqual(urls_to_reverify(self.conn, limit=1), ["https://www.gov.uk/old"])


if __name__ == "__main__":
    unittest.main()
