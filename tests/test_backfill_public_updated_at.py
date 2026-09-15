"""public_updated_at backfill tests.
Run: python3 -m unittest -v tests.test_backfill_public_updated_at"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, backfill_public_updated_at as bf


class TestExtractDate(unittest.TestCase):
    def test_prefers_public_updated_at(self):
        s = json.dumps({"public_updated_at": "2026-07-08T15:30:54+01:00",
                        "updated_at": "2026-08-01T00:00:00+01:00"})
        self.assertEqual(bf.extract_date(s), "2026-07-08T15:30:54+01:00")

    def test_falls_back_to_updated_at(self):
        s = json.dumps({"updated_at": "2026-08-01T00:00:00+01:00"})
        self.assertEqual(bf.extract_date(s), "2026-08-01T00:00:00+01:00")

    def test_none_when_absent_or_malformed(self):
        self.assertIsNone(bf.extract_date(json.dumps({"title": "x"})))
        self.assertIsNone(bf.extract_date("{bad json"))
        self.assertIsNone(bf.extract_date(""))


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            # already has a date -> untouched
            ("p0", "2020-01-01T00:00:00Z", json.dumps({"public_updated_at": "2026-01-01T00:00:00+01:00"})),
            # empty -> filled from public_updated_at
            ("p1", "", json.dumps({"public_updated_at": "2026-07-08T15:30:54+01:00"})),
            # empty, only updated_at -> filled from updated_at
            ("p2", "", json.dumps({"updated_at": "2026-06-01T00:00:00+01:00"})),
            # empty, no date -> sentinel ''
            ("p3", "", json.dumps({"title": "x"})),
        ]
        for u, pub, content in rows:
            self.conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, "
                              "public_updated_at, content) VALUES (?, 'guidance', 0, 'h', ?, ?)",
                              (f"https://www.gov.uk/{u}", pub, content))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _pub(self, u):
        return self.conn.execute("SELECT public_updated_at FROM content WHERE url=?",
                                 (f"https://www.gov.uk/{u}",)).fetchone()[0]

    def test_fills_only_empty(self):
        c = bf.build(self.conn)
        self.assertEqual(c["scanned"], 3)                       # p0 skipped (already set)
        self.assertEqual(self._pub("p0"), "2020-01-01T00:00:00Z")
        self.assertEqual(self._pub("p1"), "2026-07-08T15:30:54+01:00")
        self.assertEqual(self._pub("p2"), "2026-06-01T00:00:00+01:00")
        self.assertEqual(self._pub("p3"), "")                   # no date -> sentinel

    def test_resume_skips_dated_rows(self):
        bf.build(self.conn)
        # A fresh re-run only re-checks the residual no-date row (p3, still empty);
        # rows that got a date are skipped and their values are stable.
        c2 = bf.build(self.conn)
        self.assertEqual(c2["scanned"], 1)          # just p3
        self.assertEqual(self._pub("p1"), "2026-07-08T15:30:54+01:00")
        self.assertEqual(self._pub("p2"), "2026-06-01T00:00:00+01:00")


if __name__ == "__main__":
    unittest.main()
