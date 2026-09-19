"""search_augment.augmented_pages: source filter, provenance ordering, and the
type-ahead title filter `q` (applied across the whole stored set). No network.
Run: python3 -m unittest -v tests.test_search_augment"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus import search_augment


class TestAugmentedPages(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [
            # url, content_id, title, document_type, source, phrases(govuk), corpus_phrases(json)
            ("https://www.gov.uk/a", "c1", "Nitrate rules guidance", "guidance", "shortlister",
             None, '["nitrate"]'),
            ("https://www.gov.uk/b", "c2", "Slurry storage NITRATE notice", "notice", "both",
             "nitrate, slurry", '["nitrate", "slurry"]'),
            ("https://www.gov.uk/c", "c3", "Water quality report", "publication", "search",
             "water quality", None),
            ("https://www.gov.uk/d", "c4", "Farming payments", "guidance", "shortlister",
             None, '["payments"]'),
        ]
        for url, cid, title, dt, source, phrases, corpus in rows:
            self.conn.execute(
                "INSERT INTO category_search_pages "
                "(category_id, url, content_id, title, document_type, source, phrases, "
                "corpus_phrases, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (7, url, cid, title, dt, source, phrases, corpus, "2026-09-19T00:00:00+00:00"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_all_rows_search_first(self):
        out = search_augment.augmented_pages(self.conn, 7)
        self.assertEqual(out["total"], 4)
        # search first, then both, then shortlister
        self.assertEqual(out["rows"][0]["source"], "search")
        self.assertEqual(out["rows"][1]["source"], "both")

    def test_source_filter(self):
        out = search_augment.augmented_pages(self.conn, 7, source="shortlister")
        self.assertEqual(out["total"], 2)
        self.assertTrue(all(r["source"] == "shortlister" for r in out["rows"]))

    def test_title_filter_case_insensitive_across_whole_set(self):
        out = search_augment.augmented_pages(self.conn, 7, q="nitrate")
        # matches the shortlister row and the 'both' row regardless of case
        self.assertEqual(out["total"], 2)
        titles = sorted(r["title"] for r in out["rows"])
        self.assertEqual(titles, ["Nitrate rules guidance", "Slurry storage NITRATE notice"])

    def test_title_filter_combines_with_source(self):
        out = search_augment.augmented_pages(self.conn, 7, source="both", q="nitrate")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["rows"][0]["url"], "https://www.gov.uk/b")

    def test_title_filter_no_match(self):
        out = search_augment.augmented_pages(self.conn, 7, q="zzz-nothing")
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["rows"], [])

    def test_rows_carry_both_keyword_sets(self):
        out = search_augment.augmented_pages(self.conn, 7)
        by_url = {r["url"]: r for r in out["rows"]}
        both = by_url["https://www.gov.uk/b"]
        self.assertEqual(both["corpus_keywords"], ["nitrate", "slurry"])
        self.assertEqual(both["govuk_keywords"], ["nitrate", "slurry"])
        search_only = by_url["https://www.gov.uk/c"]
        self.assertEqual(search_only["corpus_keywords"], [])          # not in our corpus shortlist
        self.assertEqual(search_only["govuk_keywords"], ["water quality"])
        shortlister = by_url["https://www.gov.uk/a"]
        self.assertEqual(shortlister["corpus_keywords"], ["nitrate"])
        self.assertEqual(shortlister["govuk_keywords"], [])           # GOV.UK didn't return it


class TestResolveAttachmentContainers(unittest.TestCase):
    """A GOV.UK-only container page whose html_publication attachment we already hold is
    dropped, and the attachment re-tagged (both if in the shortlist, else search)."""
    CID = 9
    ATT_REL = "/government/publications/foo/the-report"

    def setUp(self):
        import json
        from govuk_corpus.canonical import canonicalise
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        self.att_url = canonicalise("https://www.gov.uk" + self.ATT_REL)
        self.container_url = "https://www.gov.uk/government/publications/foo"
        payload = {"details": {"attachments": [
            {"attachment_type": "html", "url": self.ATT_REL},
            {"attachment_type": "file", "url": "/x.pdf"},   # ignored
        ]}}
        # corpus: the attachment page (html_publication, content_id a1) and the container
        for url, cidv, content in [(self.att_url, "a1", None),
                                   (self.container_url, "cc", json.dumps(payload))]:
            self.conn.execute(
                "INSERT INTO content (url, content_id, document_type, is_redirect, content_hash, content) "
                "VALUES (?,?,?,?,?,?)", (url, cidv, "html_publication", 0, "h", content))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _search_rows(self):
        return {dict(r)["url"]: dict(r)["source"] for r in self.conn.execute(
            "SELECT url, source FROM category_search_pages WHERE category_id = ?", (self.CID,)).fetchall()}

    def _seed(self, att_source):
        # container as a GOV.UK-only row; attachment as whatever provenance compare left it
        self.conn.execute(
            "INSERT INTO category_search_pages (category_id, url, content_id, source) VALUES (?,?,?,?)",
            (self.CID, self.container_url, "cc", "search"))
        if att_source:
            self.conn.execute(
                "INSERT INTO category_search_pages (category_id, url, content_id, source) VALUES (?,?,?,?)",
                (self.CID, self.att_url, "a1", att_source))
        self.conn.commit()

    def test_attachment_in_shortlist_upgrades_to_both(self):
        self.conn.execute("INSERT INTO category_shortlist_pages (category_id, content_id, url) VALUES (?,?,?)",
                          (self.CID, "a1", self.att_url))
        self.conn.commit()
        self._seed(att_source="shortlister")
        out = search_augment.resolve_attachment_containers(self.conn, self.CID)
        rows = self._search_rows()
        self.assertNotIn(self.container_url, rows)          # container dropped
        self.assertEqual(rows.get(self.att_url), "both")    # attachment upgraded
        self.assertEqual(out["containers_dropped"], 1)
        self.assertEqual(out["attachments_both"], 1)

    def test_attachment_in_corpus_not_shortlist_tagged_search(self):
        # attachment not in the shortlist and has no search row yet
        self._seed(att_source=None)
        out = search_augment.resolve_attachment_containers(self.conn, self.CID)
        rows = self._search_rows()
        self.assertNotIn(self.container_url, rows)          # container dropped
        self.assertEqual(rows.get(self.att_url), "search")  # attachment added as GOV.UK-only
        self.assertEqual(out["attachments_search"], 1)

    def test_attachment_not_in_corpus_left_alone(self):
        # remove the attachment page from the corpus -> not resolvable
        self.conn.execute("DELETE FROM content WHERE url = ?", (self.att_url,))
        self.conn.commit()
        self._seed(att_source=None)
        out = search_augment.resolve_attachment_containers(self.conn, self.CID)
        rows = self._search_rows()
        self.assertEqual(rows.get(self.container_url), "search")   # container untouched
        self.assertEqual(out["containers_dropped"], 0)


if __name__ == "__main__":
    unittest.main()
