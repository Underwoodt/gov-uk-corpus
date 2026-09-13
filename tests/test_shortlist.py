"""Shortlist tests: query building + real results on a SQLite fixture.
Run: python3 -m unittest -v tests.test_shortlist"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus.shortlist import build_query, count, shortlist, shortlist_rows


class TestBuildQuery(unittest.TestCase):
    def test_defaults_exclude_redirects_and_non_200(self):
        sql, params = build_query()
        self.assertIn("c.is_redirect = 0", sql)
        self.assertIn("c.http_status = 200", sql)
        self.assertNotIn("JOIN page_organisations", sql)
        self.assertEqual(params, [])

    def test_org_join_and_params(self):
        sql, params = build_query(organisations=["environment-agency"])
        self.assertIn("JOIN page_organisations", sql)
        self.assertIn("organisation_slug IN (?)", sql)
        self.assertEqual(params[0], "environment-agency")

    def test_keywords_all_vs_any(self):
        sql_all, _ = build_query(keywords=["a", "b"], match="all")
        sql_any, _ = build_query(keywords=["a", "b"], match="any")
        self.assertIn("LIKE ? AND LOWER(c.content) LIKE ?", sql_all)
        self.assertIn("LIKE ? OR LOWER(c.content) LIKE ?", sql_any)

    def test_keyword_params_lowered_and_wrapped(self):
        _, params = build_query(keywords=["Slurry"])
        self.assertEqual(params, ["%slurry%"])

    def test_count_only(self):
        sql, _ = build_query(count_only=True)
        self.assertIn("COUNT(DISTINCT c.url)", sql)
        self.assertNotIn("ORDER BY", sql)


class TestShortlistResults(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            # url, document_type, is_redirect, http_status, content, org
            ("https://www.gov.uk/a", "guidance", 0, 200, "slurry storage rules", "environment-agency"),
            ("https://www.gov.uk/b", "guidance", 0, 200, "nitrate vulnerable zones", "environment-agency"),
            ("https://www.gov.uk/c", "news_story", 0, 200, "slurry spreading news", "defra"),
            ("https://www.gov.uk/d", "guidance", 1, 200, "old slurry page", "environment-agency"),  # redirect
            ("https://www.gov.uk/e", "guidance", 0, 404, "slurry missing", "environment-agency"),   # non-200
        ]
        for url, dt, red, st, content, org in rows:
            self.conn.execute(
                "INSERT INTO content (url, document_type, is_redirect, http_status, content) VALUES (?,?,?,?,?)",
                (url, dt, red, st, content))
            self.conn.execute(
                "INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                "VALUES (?,?,?,?)", (url, org, org, "primary"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_keyword_filters_and_excludes_redirect_and_non200(self):
        urls = shortlist(self.conn, keywords=["slurry"])
        # a and c match; d is a redirect, e is 404 -> excluded
        self.assertEqual(urls, ["https://www.gov.uk/a", "https://www.gov.uk/c"])

    def test_org_and_doctype(self):
        urls = shortlist(self.conn, organisations=["environment-agency"], document_types=["guidance"])
        self.assertEqual(urls, ["https://www.gov.uk/a", "https://www.gov.uk/b"])

    def test_match_all_vs_any(self):
        self.assertEqual(shortlist(self.conn, keywords=["slurry", "nitrate"], match="all"), [])
        self.assertEqual(
            shortlist(self.conn, keywords=["slurry", "nitrate"], match="any"),
            ["https://www.gov.uk/a", "https://www.gov.uk/b", "https://www.gov.uk/c"],
        )

    def test_count(self):
        self.assertEqual(count(self.conn, keywords=["slurry"]), 2)

    def test_shortlist_rows_has_url_and_title(self):
        rows = shortlist_rows(self.conn, keywords=["slurry"])
        self.assertEqual([r["url"] for r in rows], ["https://www.gov.uk/a", "https://www.gov.uk/c"])
        self.assertTrue(all("title" in r for r in rows))

    def test_build_query_include_title(self):
        sql, _ = build_query(include_title=True)
        self.assertIn("DISTINCT c.url AS url, c.title AS title", sql)

    def test_include_redirects_and_any_status(self):
        urls = shortlist(self.conn, keywords=["slurry"], include_redirects=True, any_status=True)
        self.assertIn("https://www.gov.uk/d", urls)
        self.assertIn("https://www.gov.uk/e", urls)


if __name__ == "__main__":
    unittest.main()
