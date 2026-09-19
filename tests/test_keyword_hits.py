"""Per-page keyword hits: membership stores which keywords each page matched, and the
keyword charts derive from that stored value (single source of truth). SQLite fixture.
Run: python3 -m unittest -v tests.test_keyword_hits"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import categories as cat
from govuk_corpus import category_counts, db, reporting, shortlist


import unittest as _ut


class TestKeywordScope(_ut.TestCase):
    """title_desc scope matches only the title/description; anywhere also matches the body."""
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            # url, title, description, search_text
            ("https://www.gov.uk/t", "Slurry storage guidance", "", "generic body"),   # kw in title
            ("https://www.gov.uk/d", "Farm update", "About slurry lagoons", "generic"),  # kw in description
            ("https://www.gov.uk/b", "Farm payments update", "", "slurry lagoon rules"), # kw in BODY only
        ]
        for url, title, desc, body in rows:
            self.conn.execute(
                "INSERT INTO content (url, title, description, search_text, document_type, "
                "is_redirect, content_hash) VALUES (?,?,?,?,?,?,?)",
                (url, title, desc, body, "guidance", 0, "h"))
            self.conn.execute(
                "INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                "VALUES (?,?,?,?)", (url, "defra", "defra", "primary"))
        self.conn.commit()
        self.f = dict(organisations=["defra"], document_types=["guidance"],
                      keywords=["slurry"], match="any")

    def tearDown(self):
        self.conn.close()

    def test_anywhere_matches_all_three(self):
        self.assertEqual(shortlist.count(self.conn, keyword_scope="anywhere", **self.f), 3)

    def test_title_desc_excludes_body_only(self):
        # /b (keyword only in the body) drops out; title and description hits remain.
        self.assertEqual(shortlist.count(self.conn, keyword_scope="title_desc", **self.f), 2)

    def test_default_scope_is_anywhere(self):
        self.assertEqual(shortlist.count(self.conn, **self.f), 3)


class TestKeywordHits(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        # title carries the searchable text (SQLite matches title+description+search_text).
        rows = [
            ("https://www.gov.uk/a", "Slurry storage guidance"),          # slurry
            ("https://www.gov.uk/b", "Nitrate and slurry rules"),         # slurry + nitrate
            ("https://www.gov.uk/c", "Nitrate vulnerable zones"),         # nitrate
            ("https://www.gov.uk/d", "Farm payments explained"),          # neither -> not in shortlist
        ]
        for url, title in rows:
            self.conn.execute(
                "INSERT INTO content (url, title, document_type, is_redirect, content_hash) "
                "VALUES (?,?,?,?,?)", (url, title, "guidance", 0, "h"))
            self.conn.execute(
                "INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                "VALUES (?,?,?,?)", (url, "defra", "defra", "primary"))
        self.conn.commit()
        self.cid = cat.create_category(
            self.conn, {"slug": "demo", "dept_slugs": "defra",
                        "document_type_slugs": "guidance", "keywords": "slurry,nitrate"})
        category_counts.refresh_one(self.conn, self.cid)

    def tearDown(self):
        self.conn.close()

    def _matched(self):
        return {dict(r)["url"]: sorted(json.loads(dict(r)["matched_keywords"]))
                for r in self.conn.execute(
                    "SELECT url, matched_keywords FROM category_shortlist_pages "
                    "WHERE category_id = ?", (self.cid,)).fetchall()}

    def test_membership_stores_matched_keywords(self):
        m = self._matched()
        self.assertEqual(m["https://www.gov.uk/a"], ["slurry"])
        self.assertEqual(m["https://www.gov.uk/b"], ["nitrate", "slurry"])
        self.assertEqual(m["https://www.gov.uk/c"], ["nitrate"])
        self.assertNotIn("https://www.gov.uk/d", m)   # matched neither -> excluded (match=any)

    def test_membership_rows_with_hits(self):
        rows = shortlist.membership_rows_with_hits(
            self.conn, organisations=["defra"], document_types=["guidance"],
            keywords=["slurry", "nitrate"], match="any")
        by_url = {u: sorted(h) for _cid, u, h in rows}
        self.assertEqual(by_url["https://www.gov.uk/b"], ["nitrate", "slurry"])

    def test_keyword_breakdown_from_stored(self):
        self.assertTrue(reporting.has_matched_keywords(self.conn, self.cid))
        counts = {t["keyword"]: t["count"]
                  for t in reporting.keyword_breakdown(self.conn, self.cid, ["slurry", "nitrate"])}
        self.assertEqual(counts, {"slurry": 2, "nitrate": 2})

    def test_keyword_overlap_from_stored(self):
        data = reporting.keyword_overlap(self.conn, self.cid, ["slurry", "nitrate"])
        # bit 0 = slurry, bit 1 = nitrate
        regions = {r["mask"]: r["count"] for r in data["regions"]}
        self.assertEqual(regions.get(1), 1)   # slurry only  (a)
        self.assertEqual(regions.get(2), 1)   # nitrate only (c)
        self.assertEqual(regions.get(3), 1)   # both         (b)
        self.assertIsInstance(data["sql"], str)   # stored-derivation display SQL

    def test_refresh_all_keeps_keyword_narrowing_and_hits(self):
        # The nightly path (refresh_all) must build the SAME keyword-narrowed membership as a
        # save (refresh_one), with matched_keywords populated — not a broader org+doctype set.
        category_counts.refresh_all(self.conn)
        m = self._matched()
        self.assertNotIn("https://www.gov.uk/d", m)              # keyword filter still applied
        self.assertEqual(m["https://www.gov.uk/b"], ["nitrate", "slurry"])
        self.assertEqual(len(m), 3)                              # a, b, c only

    def test_charts_fall_back_when_unstored(self):
        # Simulate pre-upgrade rows (matched_keywords never populated).
        self.conn.execute(
            "UPDATE category_shortlist_pages SET matched_keywords = NULL WHERE category_id = ?",
            (self.cid,))
        self.conn.commit()
        self.assertFalse(reporting.has_matched_keywords(self.conn, self.cid))
        counts = {t["keyword"]: t["count"]
                  for t in reporting.keyword_breakdown(self.conn, self.cid, ["slurry", "nitrate"])}
        self.assertEqual(counts, {"slurry": 2, "nitrate": 2})   # live fallback matches


if __name__ == "__main__":
    unittest.main()
