"""Tests for URL canonicalisation. Run: python3 -m unittest -v tests.test_canonical"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus.canonical import canonicalise, is_valid, path_of


class TestCanonicalise(unittest.TestCase):
    def test_promotes_bare_host_to_www(self):
        self.assertEqual(
            canonicalise("https://gov.uk/foo/bar"),
            "https://www.gov.uk/foo/bar",
        )

    def test_keeps_www(self):
        self.assertEqual(
            canonicalise("https://www.gov.uk/foo"),
            "https://www.gov.uk/foo",
        )

    def test_strips_trailing_slash(self):
        self.assertEqual(
            canonicalise("https://www.gov.uk/foo/bar/"),
            "https://www.gov.uk/foo/bar",
        )

    def test_root_keeps_slash(self):
        self.assertEqual(canonicalise("https://gov.uk/"), "https://www.gov.uk/")

    def test_drops_fragment_and_query(self):
        self.assertEqual(
            canonicalise("https://www.gov.uk/foo?x=1#section"),
            "https://www.gov.uk/foo",
        )

    def test_forces_https(self):
        self.assertEqual(
            canonicalise("http://www.gov.uk/foo"),
            "https://www.gov.uk/foo",
        )

    def test_rejects_concatenated_urls(self):
        # The real bug seen in test-scopes-3.
        self.assertIsNone(
            canonicalise(
                "https://gov.ukhttps://webarchive.nationalarchives.gov.uk/ukgwa/"
                "https://www.gov.uk/government/publications/x"
            )
        )

    def test_rejects_non_govuk_host(self):
        self.assertIsNone(canonicalise("https://example.com/foo"))
        self.assertIsNone(
            canonicalise("https://webarchive.nationalarchives.gov.uk/x")
        )

    def test_rejects_empty_and_none(self):
        self.assertIsNone(canonicalise(None))
        self.assertIsNone(canonicalise(""))
        self.assertIsNone(canonicalise("   "))

    def test_rejects_non_http_scheme(self):
        self.assertIsNone(canonicalise("ftp://www.gov.uk/foo"))
        self.assertIsNone(canonicalise("/government/publications/x"))

    def test_whitespace_trimmed(self):
        self.assertEqual(
            canonicalise("  https://gov.uk/foo  "),
            "https://www.gov.uk/foo",
        )

    def test_is_valid(self):
        self.assertTrue(is_valid("https://gov.uk/foo"))
        self.assertFalse(is_valid("https://gov.ukhttps://x.com/y"))

    def test_path_of(self):
        self.assertEqual(path_of("https://gov.uk/foo/bar/"), "/foo/bar")
        self.assertIsNone(path_of("nonsense"))


if __name__ == "__main__":
    unittest.main()
