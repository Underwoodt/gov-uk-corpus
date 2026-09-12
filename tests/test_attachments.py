"""Stage 3 tests: child/attachment link extraction (no network).
Run: python3 -m unittest -v tests.test_attachments"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus.extract import extract_child_links


class TestExtractChildLinks(unittest.TestCase):
    def test_links_children(self):
        payload = {
            "base_path": "/government/publications/x",
            "links": {"children": [{"base_path": "/government/publications/x/html-part"}]},
        }
        links, binaries = extract_child_links(payload, "https://www.gov.uk/government/publications/x")
        self.assertEqual(links, [("https://www.gov.uk/government/publications/x/html-part", "child")])
        self.assertEqual(binaries, 0)

    def test_guide_parts(self):
        payload = {"base_path": "/adi-part-1-test",
                   "details": {"parts": [{"slug": "book-test"}, {"slug": "what-to-take"}]}}
        links, _ = extract_child_links(payload, "https://www.gov.uk/adi-part-1-test")
        self.assertEqual(links, [
            ("https://www.gov.uk/adi-part-1-test/book-test", "part"),
            ("https://www.gov.uk/adi-part-1-test/what-to-take", "part"),
        ])

    def test_file_attachments_counted_not_linked(self):
        payload = {"base_path": "/p", "details": {"attachments": [
            {"attachment_type": "file", "content_type": "application/pdf", "url": "https://assets.publishing.service.gov.uk/a.pdf"},
            {"attachment_type": "html", "url": "/p/html-annex"},
        ]}}
        links, binaries = extract_child_links(payload, "https://www.gov.uk/p")
        self.assertEqual(binaries, 1)
        self.assertIn(("https://www.gov.uk/p/html-annex", "attachment"), links)

    def test_drops_self_and_external(self):
        payload = {"base_path": "/p", "links": {"children": [
            {"base_path": "/p"},                       # self -> dropped
            {"base_path": "https://example.com/x"},    # external -> dropped
            {"base_path": "/p/real"},                  # kept
        ]}}
        links, _ = extract_child_links(payload, "https://www.gov.uk/p")
        self.assertEqual(links, [("https://www.gov.uk/p/real", "child")])

    def test_empty(self):
        links, binaries = extract_child_links({"base_path": "/p"}, "https://www.gov.uk/p")
        self.assertEqual(links, [])
        self.assertEqual(binaries, 0)


class TestDbHelpers(unittest.TestCase):
    def setUp(self):
        from govuk_corpus import db
        self.db = db
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_content_exists_and_add_page_link(self):
        self.assertFalse(self.db.content_exists(self.conn, "https://www.gov.uk/a"))
        self.conn.execute("INSERT INTO content (url) VALUES (?)", ("https://www.gov.uk/a",))
        self.conn.commit()
        self.assertTrue(self.db.content_exists(self.conn, "https://www.gov.uk/a"))

        self.db.add_page_link(self.conn, "https://www.gov.uk/a", "https://www.gov.uk/a/child", "child")
        self.db.add_page_link(self.conn, "https://www.gov.uk/a", "https://www.gov.uk/a/child", "child")  # idempotent
        n = self.conn.execute("SELECT COUNT(*) AS n FROM page_links").fetchone()["n"]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
