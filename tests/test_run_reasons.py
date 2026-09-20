"""run_results joins an exclusion run to its inclusion run so both reasons show. SQLite.
Run: python3 -m unittest -v tests.test_run_reasons"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, evaluate


class TestRunReasons(unittest.TestCase):
    URL = "https://www.gov.uk/a"

    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        for rid, src, phase in [("i1", None, "Phase 1 - Inclusion"),
                                ("e1", "i1", "Phase 2 - Exclusion")]:
            self.conn.execute(
                "INSERT INTO evaluation_runs (run_id, category_id, source_run_id, phase, started_at) "
                "VALUES (?,?,?,?,?)", (rid, 1, src, phase, "2026-09-20T00:00:00+00:00"))
        self.conn.execute(
            "INSERT INTO evaluation_results (run_id, category_id, url, keep, score, reason) "
            "VALUES (?,?,?,?,?,?)", ("i1", 1, self.URL, 1, 0.9, "kept: clearly about the topic"))
        self.conn.execute(
            "INSERT INTO evaluation_results (run_id, category_id, url, keep, score, reason) "
            "VALUES (?,?,?,?,?,?)", ("e1", 1, self.URL, 0, 0.2, "[sludge] dropped: about biosolids"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_exclusion_run_carries_inclusion_reason(self):
        rows = evaluate.run_results(self.conn, "e1", source_run_id="i1")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["reason"], "[sludge] dropped: about biosolids")     # this run (exclusion)
        self.assertEqual(r["src_reason"], "kept: clearly about the topic")     # inclusion run

    def test_inclusion_run_has_no_source_reason(self):
        rows = evaluate.run_results(self.conn, "i1")
        self.assertEqual(rows[0]["reason"], "kept: clearly about the topic")
        self.assertIsNone(rows[0]["src_reason"])


if __name__ == "__main__":
    unittest.main()
