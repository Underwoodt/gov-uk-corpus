"""Stage 0 tests: sitemap parsing + frontier logic.
Run: python3 -m unittest -v tests.test_sitemap"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus.stage_sitemap import parse_index, parse_urlset

_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.gov.uk/sitemaps/sitemap_1.xml</loc><lastmod>2026-09-07T02:50:02+00:00</lastmod></sitemap>
  <sitemap><loc>https://www.gov.uk/sitemaps/sitemap_2.xml</loc></sitemap>
</sitemapindex>"""

_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.gov.uk/a</loc><lastmod>2026-01-01T00:00:00+00:00</lastmod></url>
  <url><loc>https://www.gov.uk/b</loc></url>
</urlset>"""


class TestSitemapParsing(unittest.TestCase):
    def test_parse_index(self):
        self.assertEqual(
            parse_index(_INDEX),
            ["https://www.gov.uk/sitemaps/sitemap_1.xml",
             "https://www.gov.uk/sitemaps/sitemap_2.xml"],
        )

    def test_parse_urlset(self):
        pairs = parse_urlset(_URLSET)
        self.assertEqual(pairs[0], ("https://www.gov.uk/a", "2026-01-01T00:00:00+00:00"))
        self.assertEqual(pairs[1], ("https://www.gov.uk/b", None))


class TestFrontierLogic(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def _sitemap(self, url, lastmod):
        db.upsert_sitemap(self.conn, url, "s1", lastmod)

    def _content(self, url, sitemap_lastmod):
        self.conn.execute(
            "INSERT INTO content (url, content_hash, sitemap_lastmod) VALUES (?,?,?)",
            (url, "h", sitemap_lastmod),
        )
        self.conn.commit()

    def test_frontier_selection(self):
        # never fetched -> in frontier
        self._sitemap("https://www.gov.uk/new", "2026-01-01T00:00:00+00:00")
        # no lastmod, already fetched -> NOT in frontier (fetch once only)
        self._sitemap("https://www.gov.uk/nolm", None)
        self._content("https://www.gov.uk/nolm", None)
        # fetched, sitemap lastmod unchanged -> NOT in frontier
        self._sitemap("https://www.gov.uk/same", "2026-01-01T00:00:00+00:00")
        self._content("https://www.gov.uk/same", "2026-01-01T00:00:00+00:00")
        # fetched, sitemap lastmod newer -> in frontier
        self._sitemap("https://www.gov.uk/changed", "2026-06-01T00:00:00+00:00")
        self._content("https://www.gov.uk/changed", "2026-01-01T00:00:00+00:00")
        # migrated backlog: row exists, lastmod matches, but never fetched (no hash) -> in frontier
        self._sitemap("https://www.gov.uk/backlog", "2026-01-01T00:00:00+00:00")
        self.conn.execute(
            "INSERT INTO content (url, content_hash, sitemap_lastmod) VALUES (?,?,?)",
            ("https://www.gov.uk/backlog", None, "2026-01-01T00:00:00+00:00"))
        self.conn.commit()

        urls = {r["url"] for r in db.sitemap_frontier(self.conn)}
        self.assertEqual(
            urls,
            {"https://www.gov.uk/new", "https://www.gov.uk/changed", "https://www.gov.uk/backlog"},
        )

    def test_upsert_sitemap_transitions(self):
        self.assertEqual(self._up("https://www.gov.uk/x", "2026-01-01"), "new")
        self.assertEqual(self._up("https://www.gov.uk/x", "2026-01-01"), "unchanged")
        self.assertEqual(self._up("https://www.gov.uk/x", "2026-02-01"), "updated")

    def _up(self, url, lm):
        r = db.upsert_sitemap(self.conn, url, "s1", lm)
        self.conn.commit()
        return r


if __name__ == "__main__":
    unittest.main()
