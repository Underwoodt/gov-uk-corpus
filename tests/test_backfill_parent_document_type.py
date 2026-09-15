"""Parent-document-type backfill tests.
Run: python3 -m unittest -v tests.test_backfill_parent_document_type"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, backfill_parent_document_type as bf
from govuk_corpus.shortlist import export_rows


class TestExtract(unittest.TestCase):
    def test_array_parent(self):
        s = json.dumps({"links": {"parent": [{"document_type": "guidance"}]}})
        self.assertEqual(bf.extract_parent_doctype(s), "guidance")

    def test_object_parent(self):
        s = json.dumps({"links": {"parent": {"document_type": "policy_paper"}}})
        self.assertEqual(bf.extract_parent_doctype(s), "policy_paper")

    def test_no_parent_and_malformed(self):
        self.assertIsNone(bf.extract_parent_doctype(json.dumps({"title": "x"})))
        self.assertIsNone(bf.extract_parent_doctype("{not valid json"))
        self.assertIsNone(bf.extract_parent_doctype(""))
        self.assertIsNone(bf.extract_parent_doctype(None))


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            ("https://www.gov.uk/a", "html_publication", json.dumps({"links": {"parent": [{"document_type": "guidance"}]}})),
            ("https://www.gov.uk/b", "html_publication", json.dumps({"title": "no parent"})),
            ("https://www.gov.uk/c", "html_publication", "{bad json"),
            ("https://www.gov.uk/d", "guidance", json.dumps({"links": {"parent": [{"document_type": "policy_paper"}]}})),
        ]
        for url, dt, content in rows:
            self.conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, "
                              "search_text, content) VALUES (?,?,0,'h','body',?)", (url, dt, content))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _pdt(self, url):
        return self.conn.execute("SELECT parent_document_type FROM content WHERE url=?", (url,)).fetchone()[0]

    def test_default_targets_html_publication_only(self):
        c = bf.build(self.conn)
        self.assertEqual(c["scanned"], 3)                 # only the 3 html_publication rows
        self.assertEqual(self._pdt("https://www.gov.uk/a"), "guidance")
        self.assertEqual(self._pdt("https://www.gov.uk/b"), "")   # no parent -> sentinel
        self.assertEqual(self._pdt("https://www.gov.uk/c"), "")   # malformed -> sentinel
        self.assertIsNone(self._pdt("https://www.gov.uk/d"))      # non-html untouched

    def test_idempotent_second_run_scans_nothing(self):
        bf.build(self.conn)
        c2 = bf.build(self.conn)
        self.assertEqual(c2["scanned"], 0)                # all already marked done

    def test_all_flag_scans_every_row(self):
        c = bf.build(self.conn, all_doctypes=True)
        self.assertEqual(c["scanned"], 4)
        self.assertEqual(self._pdt("https://www.gov.uk/d"), "policy_paper")

    def test_export_uses_backfilled_column(self):
        bf.build(self.conn)
        rows = export_rows(self.conn, ["url", "parent_document_type"],
                           document_types=["html_publication"])[1]
        got = {r["url"].rsplit("/", 1)[-1]: r["parent_document_type"] for r in rows}
        self.assertEqual(got["a"], "guidance")


if __name__ == "__main__":
    unittest.main()
