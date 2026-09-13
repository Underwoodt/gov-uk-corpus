"""Shortlist tests: query building + real results on a SQLite fixture.
Run: python3 -m unittest -v tests.test_shortlist"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus.shortlist import (build_query, count, shortlist, shortlist_rows,
                                    selection_funnel, _keyword_clause)


class TestBuildQuery(unittest.TestCase):
    def test_defaults_exclude_redirects_and_unfetched(self):
        sql, params = build_query()
        self.assertIn("c.is_redirect = 0", sql)
        self.assertIn("c.content_hash IS NOT NULL", sql)
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
        self.assertIn(" AND ", sql_all.split("WHERE", 1)[1])
        self.assertIn(" OR ", sql_any.split("WHERE", 1)[1])

    def test_keyword_params_lowered_and_wrapped(self):
        _, params = build_query(keywords=["Slurry"])
        self.assertEqual(params, ["%slurry%"])

    def test_count_only(self):
        sql, _ = build_query(count_only=True)
        self.assertIn("COUNT(DISTINCT c.url)", sql)
        self.assertNotIn("ORDER BY", sql)

    def test_keyword_clause_postgres_fulltext(self):
        clause, params = _keyword_clause(["slurry", "nitrate"], "any", is_pg=True)
        self.assertIn("c.search_tsv @@", clause)
        self.assertIn("plainto_tsquery('english', %s) || plainto_tsquery('english', %s)", clause)
        self.assertEqual(params, ["slurry", "nitrate"])   # raw terms; PG stems them

    def test_keyword_clause_sqlite_like_over_body(self):
        clause, params = _keyword_clause(["slurry"], "any", is_pg=False)
        self.assertIn("c.search_text", clause)
        self.assertIn("LIKE ?", clause)
        self.assertEqual(params, ["%slurry%"])

    def test_keyword_clause_all_vs_any(self):
        pg_all, _ = _keyword_clause(["a", "b"], "all", is_pg=True)
        self.assertIn("&&", pg_all)
        pg_any, _ = _keyword_clause(["a", "b"], "any", is_pg=True)
        self.assertIn("||", pg_any)


class TestShortlistResults(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            # url, document_type, is_redirect, content_hash, search_text, org
            ("https://www.gov.uk/a", "guidance", 0, "h", "slurry storage rules", "environment-agency"),
            ("https://www.gov.uk/b", "guidance", 0, "h", "nitrate vulnerable zones", "environment-agency"),
            ("https://www.gov.uk/c", "news_story", 0, "h", "slurry spreading news", "defra"),
            ("https://www.gov.uk/d", "guidance", 1, "h", "old slurry page", "environment-agency"),  # redirect
            ("https://www.gov.uk/e", "guidance", 0, None, "slurry missing", "environment-agency"),  # unfetched (no body)
        ]
        for url, dt, red, chash, text, org in rows:
            self.conn.execute(
                "INSERT INTO content (url, document_type, is_redirect, content_hash, search_text) VALUES (?,?,?,?,?)",
                (url, dt, red, chash, text))
            self.conn.execute(
                "INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                "VALUES (?,?,?,?)", (url, org, org, "primary"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_keyword_filters_and_excludes_redirect_and_unfetched(self):
        urls = shortlist(self.conn, keywords=["slurry"])
        # a and c match; d is a redirect, e is unfetched (no content_hash) -> excluded
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

    def test_selection_funnel_narrows(self):
        funnel = selection_funnel(self.conn, organisations=["environment-agency"],
                                  document_types=["guidance"])
        labels = [lbl for lbl, _ in funnel]
        self.assertEqual(labels, ["All pages", "After organisation filter", "After document-type filter"])
        counts = [n for _, n in funnel]
        self.assertGreaterEqual(counts[0], counts[1])
        self.assertGreaterEqual(counts[1], counts[2])

    def test_selection_funnel_adds_keyword_stage(self):
        funnel = selection_funnel(self.conn, keywords=["slurry"])
        self.assertEqual(funnel[-1][0], "After keyword filter")

    def test_keyword_searches_title_desc_body(self):
        # 'storage' only appears in search_text of /a -> found via title+desc+search_text
        urls = shortlist(self.conn, keywords=["storage"])
        self.assertEqual(urls, ["https://www.gov.uk/a"])

    def test_include_redirects_and_unfetched(self):
        urls = shortlist(self.conn, keywords=["slurry"], include_redirects=True, include_unfetched=True)
        self.assertIn("https://www.gov.uk/d", urls)   # redirect, now included
        self.assertIn("https://www.gov.uk/e", urls)   # unfetched, now included


if __name__ == "__main__":
    unittest.main()
