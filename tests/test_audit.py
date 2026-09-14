"""Funnel audit tests (SQLite fixture).
Run: python3 -m unittest -v tests.test_audit"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, audit


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        # org EA: a matches all; b wrong doc type; c right type but no keyword.
        # d is a DIFFERENT org -> outside the starting point, must not appear.
        rows = [
            ("https://www.gov.uk/a", "guidance", "slurry storage", "environment-agency"),
            ("https://www.gov.uk/b", "news_story", "slurry news", "environment-agency"),
            ("https://www.gov.uk/c", "guidance", "nitrate only", "environment-agency"),
            ("https://www.gov.uk/d", "guidance", "slurry storage", "defra"),
        ]
        for url, dt, text, org in rows:
            self.conn.execute(
                "INSERT INTO content (url, document_type, is_redirect, content_hash, search_text) "
                "VALUES (?,?,0,'h',?)", (url, dt, text))
            self.conn.execute(
                "INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                "VALUES (?,?,?,?)", (url, org, org, "primary"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _build(self):
        return audit.build_audit(self.conn, 1, ["environment-agency"],
                                 document_types=["guidance"], keywords=["slurry"])

    def test_requires_organisations(self):
        with self.assertRaises(ValueError):
            audit.build_audit(self.conn, 1, [])

    def test_outcomes_and_starting_point(self):
        counters = self._build()
        self.assertEqual(counters["total"], 3)                 # only the 3 EA pages, not defra
        self.assertEqual(counters[audit.OUTCOME_INCLUDED], 1)  # a
        self.assertEqual(counters[audit.OUTCOME_DOCTYPE], 1)   # b (news_story)
        self.assertEqual(counters[audit.OUTCOME_KEYWORD], 1)   # c (no slurry)

    def test_rows_and_outcome_filter(self):
        self._build()
        included = audit.audit_rows(self.conn, 1, outcome=audit.OUTCOME_INCLUDED)
        self.assertEqual([r["url"] for r in included], ["https://www.gov.uk/a"])
        self.assertNotIn("https://www.gov.uk/d",
                         [r["url"] for r in audit.audit_rows(self.conn, 1)])   # defra excluded

    def test_summary_all_outcomes_present(self):
        self._build()
        labels = [o for o, _ in audit.audit_summary(self.conn, 1)]
        self.assertEqual(labels, [audit.OUTCOME_INCLUDED, audit.OUTCOME_DOCTYPE, audit.OUTCOME_KEYWORD])

    def test_rebuild_replaces(self):
        self._build()
        again = self._build()
        self.assertEqual(again["total"], 3)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) AS n FROM category_audit WHERE category_id=1").fetchone()["n"], 3)


if __name__ == "__main__":
    unittest.main()
