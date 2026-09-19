"""HTML→text + body extraction tests.
Run: python3 -m unittest -v tests.test_text"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus.text import (ORG_MARKER, body_text, html_to_text, organisation_names,
                               organisation_refs, search_text, strip_phrases)


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


class TestOrgNameStripping(unittest.TestCase):
    DEFRA = "Department for Environment, Food & Rural Affairs"
    SLUG = "department-for-environment-food-rural-affairs"

    def _payload(self, body):
        return {"details": {"body": body},
                "links": {"primary_publishing_organisation": [
                    {"title": self.DEFRA, "base_path": "/government/organisations/" + self.SLUG}]}}

    def test_organisation_names(self):
        p = {"links": {"primary_publishing_organisation": [{"title": self.DEFRA}],
                       "organisations": [{"title": "Environment Agency"}, {"title": ""}]}}
        self.assertEqual(organisation_names(p), [self.DEFRA, "Environment Agency"])

    def test_organisation_refs_include_title_and_slug(self):
        self.assertEqual(organisation_refs(self._payload("")), [self.DEFRA, self.SLUG])

    def test_strip_phrases_marker(self):
        out = strip_phrases("A food policy from the DEPARTMENT FOR ENVIRONMENT, FOOD & RURAL "
                            "AFFAIRS today", [self.DEFRA], replacement=" " + ORG_MARKER + " ")
        self.assertEqual(out, f"A food policy from the {ORG_MARKER} today")

    def test_search_text_replaces_name_and_slug_with_marker(self):
        # 'food' survives as genuine content; the org name AND slug become the marker.
        p = self._payload("<p>New food hygiene rules from the Department for Environment, Food "
                          "&amp; Rural Affairs. See /government/organisations/"
                          "department-for-environment-food-rural-affairs.</p>")
        out = search_text(p)
        self.assertIn("food hygiene", out.lower())
        self.assertIn(ORG_MARKER, out)
        self.assertNotIn("rural affairs", out.lower())
        self.assertNotIn(self.SLUG, out)          # the bare slug is scrubbed too

    def test_search_text_keeps_body_when_no_org(self):
        self.assertEqual(search_text({"details": {"body": "<p>plain body</p>"}}), "plain body")


if __name__ == "__main__":
    unittest.main()
