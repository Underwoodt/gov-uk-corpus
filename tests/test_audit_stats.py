"""Audit-dashboard stats tests.
Run: python3 -m unittest -v tests.test_audit_stats"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, audit_stats


class TestParseIssues(unittest.TestCase):
    def test_parses_words_phrases_and_overlong(self):
        c = audit_stats.parse_issues(
            "Words to avoid: which (2), in order to (1); Over-long sentences (>25 words): 3")
        self.assertEqual(c["which"], 2)
        self.assertEqual(c["in order to"], 1)
        self.assertEqual(c["Over-long sentences"], 3)

    def test_empty(self):
        self.assertEqual(audit_stats.parse_issues(""), audit_stats.Counter())
        self.assertEqual(audit_stats.parse_issues(None), audit_stats.Counter())


class TestStats(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        self.now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        iso = lambda d: (self.now - timedelta(days=d)).strftime("%Y-%m-%dT00:00:00Z")
        rows = [
            ("p0", iso(5), 1000, 9.0, 2, "Words to avoid: which (2); Over-long sentences (>25 words): 3"),
            ("p1", iso(60), 2000, 11.0, 1, "Words to avoid: which (1)"),
            ("p2", iso(200), 3000, 13.0, 0, ""),
            ("p3", iso(500), 4000, None, 3, "Phrases to avoid: in order to (2)"),
            ("p4", None, 5000, 7.0, 1, "Words to avoid: utilise (1)"),
        ]
        for u, upd, size, ra, gds, find in rows:
            self.conn.execute(
                "INSERT INTO content (url, document_type, is_redirect, content_hash, search_text, "
                "public_updated_at, reading_age, gds_english_score, gds_findings) "
                "VALUES (?, 'guidance', 0, 'h', ?, ?, ?, ?, ?)",
                (f"https://www.gov.uk/{u}", "x" * size, upd, ra, gds, find))
            self.conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, "
                              "organisation_slug, role) VALUES (?,?,?,?)",
                              (f"https://www.gov.uk/{u}", "ea", "environment-agency", "primary"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_counts_and_averages(self):
        s = audit_stats.stats(self.conn, organisations=["environment-agency"], now=self.now)
        self.assertEqual(s["n"], 5)
        self.assertEqual(s["avg_reading_age"], 10.0)          # (9+11+13+7)/4, None ignored
        self.assertEqual(s["avg_gds"], round((2 + 1 + 0 + 3 + 1) / 5, 1))
        self.assertEqual(s["avg_size"], 3000.0)

    def test_freshness_buckets(self):
        s = audit_stats.stats(self.conn, organisations=["environment-agency"], now=self.now)
        fb = {f["bucket"]: f["count"] for f in s["freshness"]}
        self.assertEqual(fb["< 1 month"], 1)
        self.assertEqual(fb["1–3 months"], 1)
        self.assertEqual(fb["3 months–1 year"], 1)
        self.assertEqual(fb["1–2 years"], 1)
        self.assertEqual(fb["> 2 years"], 0)
        self.assertEqual(fb["Unknown"], 1)

    def test_top_issues_summed_across_pages(self):
        s = audit_stats.stats(self.conn, organisations=["environment-agency"], now=self.now)
        issues = {t["issue"]: t["count"] for t in s["top_issues"]}
        self.assertEqual(issues["which"], 3)          # 2 + 1
        self.assertEqual(issues["in order to"], 2)
        self.assertEqual(issues["utilise"], 1)
        self.assertFalse(s["issues_sampled"])


if __name__ == "__main__":
    unittest.main()
