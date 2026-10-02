"""Precomputed document_types table: rebuild, counts, ordering.
Run: python3 -m unittest -v tests.test_doc_types"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus import doc_types


def _seed(conn, rows):
    for url, dt, red in rows:
        conn.execute(
            "INSERT INTO content (url, document_type, is_redirect, content_hash) VALUES (?,?,?,?)",
            (url, dt, red, "h"))
    conn.commit()


class TestDocTypes(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_rebuild_counts_order_and_null(self):
        _seed(self.conn, [
            ("u1", "research", 0), ("u2", "research", 0), ("u3", "research", 0),
            ("u4", "guidance", 0), ("u5", "guidance", 1),          # a redirect is still counted
            ("u6", "html_publication", 0), ("u7", None, 0),        # NULL type excluded
        ])
        n = doc_types.refresh_document_types(self.conn)
        self.assertEqual(n, 3)                                      # research, guidance, html_publication
        counts = doc_types.all_counts(self.conn)
        self.assertEqual(counts[0], {"type": "research", "count": 3})   # largest first
        self.assertEqual({c["type"]: c["count"] for c in counts},
                         {"research": 3, "guidance": 2, "html_publication": 1})
        # The whole corpus query counts all rows (including the redirect), per spec.
        self.assertEqual(doc_types.known_types(self.conn),
                         {"research", "guidance", "html_publication"})

    def test_rebuild_is_idempotent(self):
        _seed(self.conn, [("u1", "research", 0)])
        doc_types.refresh_document_types(self.conn)
        doc_types.refresh_document_types(self.conn)   # replaces, no duplicate-key error
        self.assertEqual(doc_types.all_counts(self.conn), [{"type": "research", "count": 1}])

    def test_empty_before_first_refresh(self):
        self.assertEqual(doc_types.all_counts(self.conn), [])
        self.assertEqual(doc_types.known_types(self.conn), set())


if __name__ == "__main__":
    unittest.main()
