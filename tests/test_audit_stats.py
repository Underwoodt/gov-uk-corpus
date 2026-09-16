"""Audit-dashboard stats tests.
Run: python3 -m unittest -v tests.test_audit_stats"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, audit_stats


def _checks(words, **counts):
    return json.dumps({"words": words, "counts": counts})


class TestStats(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        self.now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        iso = lambda d: (self.now - timedelta(days=d)).strftime("%Y-%m-%dT00:00:00Z")
        # url, updated, size, reading_age, impact, stars, gds_checks JSON
        rows = [
            ("p0", iso(5), 1000, 9.0, 8.0, 3, _checks(300, words_to_avoid=2, long_sentences=3)),
            ("p1", iso(60), 2000, 11.0, 2.0, 4, _checks(400, words_to_avoid=1)),
            ("p2", iso(200), 3000, 13.0, 0.0, 5, _checks(500)),
            ("p3", iso(500), 4000, None, 1.6, 2, _checks(200, phrases_to_avoid=2)),
            ("p4", None, 5000, 7.0, 2.0, 4, _checks(600, words_to_avoid=1)),
        ]
        for u, upd, size, ra, impact, stars, checks in rows:
            self.conn.execute(
                "INSERT INTO content (url, document_type, is_redirect, content_hash, search_text, "
                "public_updated_at, reading_age, gds_english_score, gds_stars, gds_checks) "
                "VALUES (?, 'guidance', 0, 'h', ?, ?, ?, ?, ?, ?)",
                (f"https://www.gov.uk/{u}", "x" * size, upd, ra, impact, stars, checks))
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
        self.assertEqual(s["avg_stars"], round((3 + 4 + 5 + 2 + 4) / 5, 1))   # 3.6
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

    def test_top_classes_summed_by_impact(self):
        s = audit_stats.stats(self.conn, organisations=["environment-agency"], now=self.now)
        by_key = {t["key"]: t for t in s["top_classes"]}
        self.assertEqual(by_key["words_to_avoid"]["count"], 4)     # 2 + 1 + 1
        self.assertEqual(by_key["long_sentences"]["count"], 3)
        self.assertEqual(by_key["phrases_to_avoid"]["count"], 2)
        # sorted by impact: words_to_avoid (weight 2, 4 hits) tops long_sentences
        self.assertEqual(s["top_classes"][0]["key"], "words_to_avoid")
        self.assertIn("name", s["top_classes"][0])
        self.assertIn("reason", s["top_classes"][0])
        self.assertFalse(s["issues_sampled"])


if __name__ == "__main__":
    unittest.main()
