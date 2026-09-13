"""Dashboard metrics tests (SQLite fixture).
Run: python3 -m unittest -v tests.test_metrics"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, metrics


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        # content: 3 fetched (hash set), 1 backlog (no hash), 1 redirect
        rows = [
            ("https://www.gov.uk/a", "h1", "sitemap", 0),
            ("https://www.gov.uk/b", "h2", "sitemap", 0),
            ("https://www.gov.uk/c", "h3", "attachment", 0),
            ("https://www.gov.uk/d", None, "sitemap", 0),   # backlog
            ("https://www.gov.uk/e", "h5", "redirect", 1),   # redirect
        ]
        for url, h, src, red in rows:
            self.conn.execute(
                "INSERT INTO content (url, content_hash, source, is_redirect) VALUES (?,?,?,?)",
                (url, h, src, red))
        for u in ("https://www.gov.uk/a", "https://www.gov.uk/b", "https://www.gov.uk/d", "https://www.gov.uk/z"):
            self.conn.execute("INSERT INTO sitemap (url, lastmod) VALUES (?, ?)", (u, "2026-01-01T00:00:00+00:00"))
        self.conn.execute("INSERT INTO page_links (parent_url, child_url, relation) VALUES (?,?,?)",
                          ("https://www.gov.uk/a", "https://www.gov.uk/c", "child"))
        # a completed align run + a running one
        self.conn.execute(
            "INSERT INTO runs (run_id, started_at, finished_at, status, stage, counters) VALUES (?,?,?,?,?,?)",
            ("run-aaaaaaaa", "2026-09-12T10:00:00+00:00", "2026-09-12T10:02:30+00:00", "complete",
             "align", json.dumps({"seen": 10, "new": 8, "unchanged": 1, "error": 1})))
        self.conn.execute(
            "INSERT INTO runs (run_id, started_at, status, stage) VALUES (?,?,?,?)",
            ("run-bbbbbbbb", "2026-09-12T11:00:00+00:00", "running", "align"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_corpus_totals(self):
        t = metrics.corpus_totals(self.conn)
        self.assertEqual(t["total"], 5)
        self.assertEqual(t["fetched"], 4)
        self.assertEqual(t["unfetched"], 1)
        self.assertEqual(t["redirects"], 1)
        self.assertEqual(t["page_links"], 1)
        # backlog: sitemap urls not in content (/z) + in content but no hash (/d) = 2
        self.assertEqual(t["backlog_remaining"], 2)
        self.assertEqual(t["pct_fetched"], 80.0)

    def test_source_breakdown(self):
        b = dict(metrics.source_breakdown(self.conn))
        self.assertEqual(b["sitemap"], 3)
        self.assertEqual(b["attachment"], 1)
        self.assertEqual(b["redirect"], 1)

    def test_recent_runs_and_health(self):
        runs = metrics.recent_runs(self.conn, n=7)
        self.assertEqual(len(runs), 2)
        # newest first -> the running one
        self.assertEqual(runs[0]["status"], "running")
        self.assertIsNone(runs[0]["elapsed_s"])
        done = runs[1]
        self.assertEqual(done["elapsed_s"], 150.0)     # 2m30s
        self.assertEqual(done["errors"], 1)
        self.assertEqual(done["processed"], 9)         # new(8)+unchanged(1); seen is meta
        self.assertEqual(done["success_rate"], 90.0)   # 9/10

    def test_active_runs(self):
        self.assertEqual(metrics.active_runs(self.conn), 1)


if __name__ == "__main__":
    unittest.main()
