"""Link-gap (missing-corpus-links) tests.
Run: python3 -m unittest -v tests.test_link_gaps"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, link_gaps


def body(*hrefs):
    return json.dumps({"details": {"body": "".join(f'<a href="{h}">x</a>' for h in hrefs)}})


class TestExtract(unittest.TestCase):
    def test_extracts_govuk_only(self):
        links = link_gaps.extract_govuk_links(
            body("/a", "https://www.gov.uk/b?q=1#frag", "https://example.com/x", "mailto:a@b", "#top"))
        self.assertEqual(set(links), {"https://www.gov.uk/a", "https://www.gov.uk/b"})  # query/frag stripped

    def test_parts_body_and_malformed(self):
        payload = json.dumps({"details": {"parts": [{"body": '<a href="/part-page">p</a>'}]}})
        self.assertEqual(link_gaps.extract_govuk_links(payload), ["https://www.gov.uk/part-page"])
        self.assertEqual(link_gaps.extract_govuk_links("{bad"), [])
        self.assertEqual(link_gaps.extract_govuk_links(""), [])


class TestFindMissing(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            ("p0", body("/in-corpus", "/missing-a", "https://example.com/x")),
            ("p1", body("/missing-a", "https://www.gov.uk/missing-b?q=1")),
            ("in-corpus", "{}"),   # target that IS in the corpus
        ]
        for u, content in rows:
            self.conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, "
                              "search_text, content) VALUES (?, 'guidance', 0, 'h', 'slurry', ?)",
                              (f"https://www.gov.uk/{u}", content))
            self.conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, "
                              "organisation_slug, role) VALUES (?,?,?,?)",
                              (f"https://www.gov.uk/{u}", "ea", "environment-agency", "primary"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_missing_ranked_by_link_count(self):
        res = link_gaps.find_missing_links(self.conn, organisations=["environment-agency"],
                                           document_types=["guidance"], keywords=["slurry"], match="any")
        m = {x["url"]: x["count"] for x in res["missing"]}
        self.assertEqual(m.get("https://www.gov.uk/missing-a"), 2)   # p0 + p1
        self.assertEqual(m.get("https://www.gov.uk/missing-b"), 1)
        self.assertNotIn("https://www.gov.uk/in-corpus", m)          # exists -> not missing
        self.assertEqual(res["missing"][0]["url"], "https://www.gov.uk/missing-a")  # ranked
        self.assertFalse(res["sampled"])


if __name__ == "__main__":
    unittest.main()
