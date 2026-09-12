"""Stage 2 tests: redirect chain resolution (no network).
Run: python3 -m unittest -v tests.test_redirects"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus.stage_redirects import resolve_chain


def _redirect(dest_path):
    return {"document_type": "redirect", "schema_name": "redirect",
            "redirects": [{"destination": dest_path, "path": "/x", "type": "exact"}]}


def _page():
    return {"document_type": "guidance", "schema_name": "detailed_guide"}


class TestResolveChain(unittest.TestCase):
    def _fetch(self, table):
        def fn(url):
            if url not in table:
                return 404, None
            status, payload = table[url]
            return status, payload
        return fn

    def test_single_hop(self):
        table = {
            "https://www.gov.uk/old": (200, _redirect("/new")),
            "https://www.gov.uk/new": (200, _page()),
        }
        r = resolve_chain(self._fetch(table), "https://www.gov.uk/old")
        self.assertEqual(r["final_url"], "https://www.gov.uk/new")
        self.assertIsNone(r["error"])
        self.assertEqual(r["hops"], ["https://www.gov.uk/old", "https://www.gov.uk/new"])

    def test_multi_hop(self):
        table = {
            "https://www.gov.uk/a": (200, _redirect("/b")),
            "https://www.gov.uk/b": (200, _redirect("/c")),
            "https://www.gov.uk/c": (200, _page()),
        }
        r = resolve_chain(self._fetch(table), "https://www.gov.uk/a")
        self.assertEqual(r["final_url"], "https://www.gov.uk/c")
        self.assertIsNone(r["error"])

    def test_circular(self):
        table = {
            "https://www.gov.uk/a": (200, _redirect("/b")),
            "https://www.gov.uk/b": (200, _redirect("/a")),
        }
        r = resolve_chain(self._fetch(table), "https://www.gov.uk/a")
        self.assertIsNone(r["final_url"])
        self.assertEqual(r["error"], "circular")

    def test_max_depth(self):
        # Build a long non-repeating chain longer than the cap.
        table = {}
        for i in range(15):
            table[f"https://www.gov.uk/n{i}"] = (200, _redirect(f"/n{i+1}"))
        r = resolve_chain(self._fetch(table), "https://www.gov.uk/n0", max_depth=5)
        self.assertEqual(r["error"], "max_depth")

    def test_gone(self):
        table = {"https://www.gov.uk/old": (200, _redirect("/dead"))}
        # /dead not in table -> 404
        r = resolve_chain(self._fetch(table), "https://www.gov.uk/old")
        self.assertEqual(r["final_url"], "https://www.gov.uk/dead")
        self.assertEqual(r["error"], "not_found")

    def test_external_destination(self):
        table = {"https://www.gov.uk/old": (200, _redirect("https://example.com/x"))}
        r = resolve_chain(self._fetch(table), "https://www.gov.uk/old")
        self.assertEqual(r["error"], "external_or_bad_destination")


if __name__ == "__main__":
    unittest.main()
