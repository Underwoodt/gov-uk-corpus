"""Shortlist tests: query building + real results on a SQLite fixture.
Run: python3 -m unittest -v tests.test_shortlist"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus.shortlist import (build_query, count, shortlist, shortlist_rows,
                                    selection_funnel, _keyword_clause, interpolate_sql)


class TestBuildQuery(unittest.TestCase):
    def test_defaults_exclude_redirects_and_unfetched(self):
        sql, params = build_query()
        self.assertIn("c.is_redirect = 0", sql)
        self.assertIn("c.content_hash IS NOT NULL", sql)
        self.assertNotIn("JOIN page_organisations", sql)
        self.assertEqual(params, [])

    def test_org_exists_and_params(self):
        sql, params = build_query(organisations=["environment-agency"])
        self.assertIn("EXISTS (SELECT 1 FROM page_organisations", sql)
        self.assertIn("organisation_slug IN (?)", sql)
        self.assertNotIn("DISTINCT", sql)   # EXISTS avoids row fan-out
        self.assertEqual(params[0], "environment-agency")

    def test_pg_count_uses_exists_semijoin(self):
        # Counts and selects both use the EXISTS semi-join (no CTE, no MATERIALIZED) —
        # the fastest form measured on the live corpus with fresh stats.
        import govuk_corpus.shortlist as sl
        saved_pg, saved_p = sl._IS_PG, sl._P
        sl._IS_PG, sl._P = True, "%s"
        try:
            sql, params = sl.build_query(count_only=True,
                                         organisations=["environment-agency"],
                                         document_types=["guidance", "news"])
            self.assertIn("EXISTS (SELECT 1 FROM page_organisations", sql)
            self.assertNotIn("MATERIALIZED", sql)
            self.assertNotIn("WITH org_pages", sql)
            self.assertNotIn("JOIN org_pages", sql)
            self.assertEqual(params, ["environment-agency", "guidance", "news"])
        finally:
            sl._IS_PG, sl._P = saved_pg, saved_p

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
        # Counts are per distinct content_id (url fallback), not per url.
        self.assertIn("COUNT(DISTINCT COALESCE(c.content_id, c.url))", sql)
        self.assertNotIn("ORDER BY", sql)

    def test_keyword_clause_postgres_fulltext(self):
        clause, params = _keyword_clause(["slurry", "nitrate"], "any", is_pg=True)
        self.assertIn("c.search_tsv @@", clause)
        # phraseto_tsquery => multi-word terms match as an adjacent phrase (spaces -> <->)
        self.assertIn("phraseto_tsquery('english', %s) || phraseto_tsquery('english', %s)", clause)
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

    def test_interpolate_sql_substitutes_and_quotes(self):
        out = interpolate_sql("WHERE a IN (%s,%s) AND n = %s AND x IS %s",
                              ["environment-agency", "de'fra", 10000, None], is_pg=True)
        self.assertIn("'environment-agency'", out)
        self.assertIn("'de''fra'", out)   # single quote escaped
        self.assertIn("= 10000", out)     # number unquoted
        self.assertIn("IS NULL", out)     # None -> NULL
        self.assertNotIn("%s", out)


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

    def test_doctype_filter_matches_html_publication_by_parent(self):
        # An html_publication (a publication's content body) matches on its PARENT's type:
        # parent guidance is selected -> included; parent news_story is not -> excluded.
        self.conn.execute("INSERT INTO content (url, document_type, parent_document_type, is_redirect, "
                          "content_hash, search_text, title) "
                          "VALUES ('https://www.gov.uk/hg','html_publication','guidance',0,'h','slurry','HG')")
        self.conn.execute("INSERT INTO content (url, document_type, parent_document_type, is_redirect, "
                          "content_hash, search_text, title) "
                          "VALUES ('https://www.gov.uk/hn','html_publication','news_story',0,'h','slurry','HN')")
        self.conn.commit()
        urls = shortlist(self.conn, document_types=["guidance"], keywords=["slurry"])
        self.assertIn("https://www.gov.uk/hg", urls)        # parent=guidance -> included
        self.assertNotIn("https://www.gov.uk/hn", urls)     # parent=news_story -> excluded

    def test_results_derived_columns(self):
        from datetime import datetime, timezone, timedelta
        from govuk_corpus.shortlist import export_rows
        now = datetime.now(timezone.utc)
        iso = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%dT12:00:00Z")
        # /g guidance recent; /h html_publication with a parent, old.
        self.conn.execute("INSERT INTO content (url, document_type, parent_document_type, is_redirect, "
                          "content_hash, search_text, title, public_updated_at) "
                          "VALUES ('https://www.gov.uk/g','guidance','',0,'h','slurry','G',?)", (iso(5),))
        self.conn.execute("INSERT INTO content (url, document_type, parent_document_type, is_redirect, "
                          "content_hash, search_text, title, public_updated_at) "
                          "VALUES ('https://www.gov.uk/h','html_publication','policy_paper',0,'h','slurry','H',?)", (iso(500),))
        self.conn.commit()
        _keys, rows = export_rows(self.conn, ["url", "effective_document_type", "last_update_band"],
                                  document_types=["guidance", "html_publication"], keywords=["slurry"])
        got = {r["url"].rsplit("/", 1)[-1]: r for r in rows}
        self.assertEqual(got["g"]["effective_document_type"], "guidance")
        self.assertEqual(got["h"]["effective_document_type"], "policy_paper")   # html_publication -> parent
        self.assertEqual(got["g"]["last_update_band"], "< 1 month")
        self.assertEqual(got["h"]["last_update_band"], "1-2 years")

    def test_org_breakdown_counts_per_org_with_overlap(self):
        from govuk_corpus.shortlist import org_breakdown
        # /a (slurry, guidance) is environment-agency in setUp; also tag it defra (overlap),
        # and add /f a defra-only slurry guidance page.
        self.conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, search_text) "
                          "VALUES ('https://www.gov.uk/f','guidance',0,'h','slurry rules')")
        self.conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, "
                          "organisation_slug, role) VALUES ('https://www.gov.uk/a','defra','defra','secondary')")
        self.conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, "
                          "organisation_slug, role) VALUES ('https://www.gov.uk/f','defra','defra','primary')")
        self.conn.commit()
        res = org_breakdown(self.conn, organisations=["environment-agency", "defra"],
                            document_types=["guidance"], keywords=["slurry"], match="any")
        counts = {r["organisation"]: r["count"] for r in res}
        self.assertEqual(counts["environment-agency"], 1)   # /a
        self.assertEqual(counts["defra"], 2)                 # /a (overlap) + /f
        self.assertEqual(org_breakdown(self.conn, organisations=[]), [])

    def test_per_keyword_counts_for_breakdown(self):
        # The "which terms matched" breakdown counts each keyword on its own within
        # the org/doctype set; independent counts, so they can overlap.
        base = dict(organisations=["environment-agency"], document_types=["guidance"])
        self.assertEqual(count(self.conn, keywords=["slurry"], match="any", **base), 1)   # only /a
        self.assertEqual(count(self.conn, keywords=["nitrate"], match="any", **base), 1)  # only /b
        self.assertEqual(count(self.conn, keywords=["whey"], match="any", **base), 0)     # none

    def test_shortlist_rows_has_url_and_title(self):
        rows = shortlist_rows(self.conn, keywords=["slurry"])
        self.assertEqual([r["url"] for r in rows], ["https://www.gov.uk/a", "https://www.gov.uk/c"])
        self.assertTrue(all("title" in r for r in rows))

    def test_build_query_include_title(self):
        sql, _ = build_query(include_title=True)
        self.assertIn("c.url AS url, c.title AS title", sql)

    def test_selection_funnel_stages_and_monotonic(self):
        funnel = selection_funnel(self.conn, organisations=["environment-agency"],
                                  document_types=["guidance"])
        labels = [lbl for lbl, _ in funnel]
        self.assertEqual(labels, ["All pages", "After organisation filter",
                                  "After document-type filter", "After keyword filter"])
        counts = [n for _, n in funnel]
        # each stage narrows (or holds) the previous
        for a, b in zip(counts, counts[1:]):
            self.assertGreaterEqual(a, b)

    def test_selection_funnel_keyword_row_always_present(self):
        # no keywords -> keyword row equals the document-type row
        funnel = selection_funnel(self.conn, organisations=["environment-agency"],
                                  document_types=["guidance"])
        self.assertEqual(funnel[-1][0], "After keyword filter")
        self.assertEqual(funnel[-1][1], funnel[-2][1])

    def test_keyword_searches_title_desc_body(self):
        # 'storage' only appears in search_text of /a -> found via title+desc+search_text
        urls = shortlist(self.conn, keywords=["storage"])
        self.assertEqual(urls, ["https://www.gov.uk/a"])

    def test_parent_document_type_from_content_json(self):
        import json as J
        from govuk_corpus.shortlist import export_rows
        cases = [
            ("https://www.gov.uk/pa", J.dumps({"links": {"parent": [{"document_type": "guidance"}]}}), "guidance"),
            ("https://www.gov.uk/po", J.dumps({"links": {"parent": {"document_type": "policy_paper"}}}), "policy_paper"),
            ("https://www.gov.uk/pn", J.dumps({"title": "x"}), None),
            ("https://www.gov.uk/pm", "{not valid json", None),   # must not error the export
        ]
        for url, content, _ in cases:
            self.conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, "
                              "search_text, content) VALUES (?, 'html_publication', 0, 'h', 'body', ?)",
                              (url, content))
        self.conn.commit()
        rows = export_rows(self.conn, ["url", "parent_document_type"], document_types=["html_publication"])[1]
        got = {r["url"].rsplit("/", 1)[-1]: r["parent_document_type"] for r in rows}
        self.assertEqual(got["pa"], "guidance")
        self.assertEqual(got["po"], "policy_paper")
        self.assertIsNone(got["pn"])
        self.assertIsNone(got["pm"])   # malformed JSON -> NULL, not a crash

    def test_include_redirects_and_unfetched(self):
        urls = shortlist(self.conn, keywords=["slurry"], include_redirects=True, include_unfetched=True)
        self.assertIn("https://www.gov.uk/d", urls)   # redirect, now included
        self.assertIn("https://www.gov.uk/e", urls)   # unfetched, now included


if __name__ == "__main__":
    unittest.main()
