"""HTML→text + body extraction tests.
Run: python3 -m unittest -v tests.test_text"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus.text import body_text, html_to_text


class TestHtmlToText(unittest.TestCase):
    def test_strips_tags_and_entities(self):
        html = "<p>Store <b>slurry</b> &amp; silage</p><script>var x=1</script>"
        self.assertEqual(html_to_text(html), "Store slurry & silage")

    def test_block_tags_add_space(self):
        self.assertEqual(html_to_text("<li>one</li><li>two</li>"), "one two")

    def test_empty(self):
        self.assertEqual(html_to_text(""), "")
        self.assertEqual(html_to_text(None), "")


class TestBodyText(unittest.TestCase):
    def test_details_body(self):
        payload = {"details": {"body": "<p>nitrate vulnerable zones</p>"}}
        self.assertEqual(body_text(payload), "nitrate vulnerable zones")

    def test_parts(self):
        payload = {"details": {"parts": [
            {"slug": "a", "body": "<p>part one</p>"},
            {"slug": "b", "body": "<p>part two</p>"},
        ]}}
        self.assertEqual(body_text(payload), "part one part two")

    def test_none(self):
        self.assertEqual(body_text({"details": {}}), "")
        self.assertEqual(body_text({}), "")


if __name__ == "__main__":
    unittest.main()
