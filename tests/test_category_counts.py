"""Precomputed category page-count tests (SQLite fixture).
Run: python3 -m unittest -v tests.test_category_counts"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import categories as cat
from govuk_corpus import category_counts, db


class TestCategoryCounts(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            # url, document_type, is_redirect, content_hash, org
            ("https://www.gov.uk/a", "guidance", 0, "h", "environment-agency"),
            ("https://www.gov.uk/b", "guidance", 0, "h", "environment-agency"),
            ("https://www.gov.uk/c", "news_story", 0, "h", "environment-agency"),  # wrong doctype
            ("https://www.gov.uk/d", "guidance", 1, "h", "environment-agency"),    # redirect
            ("https://www.gov.uk/e", "guidance", 0, None, "environment-agency"),   # unfetched
        ]
        for url, dt, red, chash, org in rows:
            self.conn.execute(
                "INSERT INTO content (url, document_type, is_redirect, content_hash) VALUES (?,?,?,?)",
                (url, dt, red, chash))
            self.conn.execute(
                "INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                "VALUES (?,?,?,?)", (url, org, org, "primary"))
        self.conn.commit()
        self.cid = cat.create_category(
            self.conn, {"slug": "demo", "dept_slugs": "environment-agency",
                        "document_type_slugs": "guidance"})

    def tearDown(self):
        self.conn.close()

    def test_empty_before_refresh(self):
        self.assertEqual(category_counts.get_counts(self.conn), {})

    def test_refresh_all_stores_usable_count(self):
        summary = category_counts.refresh_all(self.conn)
        self.assertEqual(summary, {"categories": 1, "updated": 1})
        counts = category_counts.get_counts(self.conn)
        # only a,b qualify (c wrong doctype, d redirect, e unfetched)
        self.assertEqual(counts[self.cid]["pages_kept"], 2)
        self.assertTrue(counts[self.cid]["computed_at"])

    def test_refresh_one_returns_and_stores(self):
        self.assertEqual(category_counts.refresh_one(self.conn, self.cid), 2)
        self.assertEqual(category_counts.get_counts(self.conn)[self.cid]["pages_kept"], 2)

    def test_refresh_one_upserts(self):
        category_counts.refresh_one(self.conn, self.cid)
        first = category_counts.get_counts(self.conn)[self.cid]["computed_at"]
        # a second refresh must not raise (ON CONFLICT update) and keeps one row
        category_counts.refresh_one(self.conn, self.cid)
        self.assertEqual(len(category_counts.get_counts(self.conn)), 1)
        self.assertTrue(first)

    def test_refresh_one_missing_category(self):
        self.assertIsNone(category_counts.refresh_one(self.conn, 999))

    def test_refresh_all_prunes_deleted_categories(self):
        category_counts.refresh_all(self.conn)
        self.assertIn(self.cid, category_counts.get_counts(self.conn))
        cat.delete_category(self.conn, self.cid)
        summary = category_counts.refresh_all(self.conn)
        self.assertEqual(summary, {"categories": 0, "updated": 0})
        self.assertEqual(category_counts.get_counts(self.conn), {})


if __name__ == "__main__":
    unittest.main()
