"""In-app Help renderer tests.
Run: python3 -m unittest -v tests.test_help_docs"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import help_docs as h


class TestListing(unittest.TestCase):
    def test_lists_journeys_in_order(self):
        js = h.list_journeys()
        slugs = [j["slug"] for j in js]
        self.assertEqual(slugs[:4], ["1-create-an-account", "2-what-is-a-shortlist",
                                     "3-create-a-shortlist", "4-run-the-ai"])
        self.assertTrue(all(j["title"] for j in js))
        self.assertIn("4-run-the-ai", slugs)

    def test_readme_excluded(self):
        self.assertNotIn("README", [j["slug"] for j in h.list_journeys()])


class TestSlugSafety(unittest.TestCase):
    def test_unknown_and_traversal_rejected(self):
        self.assertIsNone(h.render("99-nope"))
        self.assertIsNone(h.render("../../etc/passwd"))
        self.assertIsNone(h.render("../README"))
        self.assertIsNone(h.render("4-run-the-ai/../../secret"))
        self.assertIsNone(h.render(""))


class TestRender(unittest.TestCase):
    def setUp(self):
        self.title, self.body = h.render("4-run-the-ai")

    def test_title(self):
        self.assertTrue(self.title.startswith("Journey 4"))

    def test_presenter_bits_stripped(self):
        self.assertNotIn("Say:", self.body)               # narration removed
        self.assertNotIn("30-second pitch", self.body)    # pitch section removed

    def test_internal_links_rewritten(self):
        self.assertIn('href="/help/5-download-a-shortlist"', self.body)
        # a cross-doc anchor link (Journey 7 → Journey 6, section 6.4)
        _, b7 = h.render("7-edit-filter-parameters")
        self.assertIn('href="/help/6-how-was-this-built#64-letting-the-ai-reframe-your-question"', b7)

    def test_heading_ids_present_and_match_anchor_scheme(self):
        # A section heading gets an id matching the GitHub-style anchor (colon-titled headings).
        self.assertIn('id="41-start-a-run"', self.body)

    def test_images_become_captions_when_file_absent(self):
        # Journey 4 has no screenshot files, so its images render as captions, not broken <img>.
        self.assertIn("help-figure", self.body)
        self.assertNotIn("<img", self.body)

    def test_existing_screenshot_renders_as_img(self):
        # Journey 1's screenshots are committed, so they render as real served images.
        _, b1 = h.render("1-create-an-account")
        self.assertIn('<img src="/help/screenshots/1-1-sign-in.png"', b1)
        self.assertIn('figure class="help-shot"', b1)

    def test_screenshot_path_safety(self):
        self.assertTrue(h.screenshot_path("1-1-sign-in.png"))   # committed file
        self.assertIsNone(h.screenshot_path("../secret.png"))   # traversal
        self.assertIsNone(h.screenshot_path("nope.png"))        # missing
        self.assertIsNone(h.screenshot_path("evil.txt"))        # non-image extension
        self.assertIsNone(h.screenshot_path(""))

    def test_kept_blockquote_note_rendered(self):
        # The interface-level note is a non-Say blockquote → kept as a styled note (Journey 6).
        _, b6 = h.render("6-how-was-this-built")
        self.assertIn("help-note", b6)
        self.assertIn("simple interface", b6)

    def test_wrapped_list_items_not_split(self):
        # A bullet whose text wraps to a second line stays one <li> (continuation folded in,
        # not left as a stray paragraph).
        self.assertIn("keeps anything above zero", self.body)

    def test_in_one_breath_heading_dropped(self):
        self.assertNotIn("In one breath", self.body)


class TestHelpers(unittest.TestCase):
    def test_heading_id(self):
        self.assertEqual(h._heading_id("6.4 — Letting the AI reframe your question"),
                         "64--letting-the-ai-reframe-your-question")

    def test_inline_escapes_and_formats(self):
        out = h._inline("A **bold** and `x<y` and <script>", "4-run-the-ai")
        self.assertIn("<strong>bold</strong>", out)
        self.assertIn("<code>x&lt;y</code>", out)
        self.assertIn("&lt;script&gt;", out)   # raw HTML escaped
        self.assertNotIn("<script>", out)

    def test_inline_external_link_gets_target(self):
        out = h._inline("[GOV.UK](https://www.gov.uk)", "x")
        self.assertIn('href="https://www.gov.uk"', out)
        self.assertIn('target="_blank"', out)


if __name__ == "__main__":
    unittest.main()
