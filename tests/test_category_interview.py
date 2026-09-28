"""Category-interview parsing helpers (document-type matching, slug/field parsing).

These back the shortlist form's server-side slug validation and document-type list; the
interactive shortlist-builder assistant that once also used them has been removed.
Run: python3 -m unittest -v tests.test_category_interview"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import category_interview as ci

class TestParseFields(unittest.TestCase):
    def test_extracts_fenced_json(self):
        reply = ('Great, here is a draft.\n\n```json\n{"slug": "farm-slurry", '
                 '"dept_slugs": "environment-agency", "include_child_orgs": true, '
                 '"keywords": "slurry, animal manure", "inclusion_context": "About slurry."}\n```\n'
                 "It looks solid; double-check the org list.")
        f = ci.parse_fields(reply)
        self.assertEqual(f["slug"], "farm-slurry")
        self.assertEqual(f["dept_slugs"], "environment-agency")
        self.assertTrue(f["include_child_orgs"])
        self.assertEqual(f["keywords"], "slurry, animal manure")
        self.assertNotIn("owner_email", f)   # omitted keys stay omitted

    def test_none_before_draft(self):
        self.assertIsNone(ci.parse_fields("What organisations publish this?"))
        self.assertIsNone(ci.parse_fields(""))

    def test_ignores_unknown_keys(self):
        f = ci.parse_fields('```json\n{"slug": "x", "dept_slugs": "ea", "bogus": 1}\n```')
        self.assertEqual(set(f), {"slug", "dept_slugs"})


class TestParseSuggestion(unittest.TestCase):
    def test_extracts_suggest_block(self):
        reply = ("**Organisations**\nWhich bodies publish this?\n\n"
                 "```suggest\nenvironment-agency, department-for-environment-food-rural-affairs\n```")
        self.assertEqual(ci.parse_suggestion(reply),
                         "environment-agency, department-for-environment-food-rural-affairs")

    def test_multiline_context_suggestion(self):
        reply = "**Include context**\nHere's a draft.\n```suggest\nPages about storing farm slurry.\nAnd the rules farmers follow.\n```"
        self.assertEqual(ci.parse_suggestion(reply),
                         "Pages about storing farm slurry.\nAnd the rules farmers follow.")

    def test_none_when_absent(self):
        self.assertIsNone(ci.parse_suggestion("Just a question, no suggestion."))
        self.assertIsNone(ci.parse_suggestion(""))
        self.assertIsNone(ci.parse_suggestion("```suggest\n\n```"))   # empty block -> None


class TestEditModePrompt(unittest.TestCase):
    def test_plain_prompt_unchanged(self):
        self.assertEqual(ci.system_prompt(), ci.SYSTEM_PROMPT)
        self.assertEqual(ci.system_prompt(None), ci.SYSTEM_PROMPT)

    def test_edit_prompt_includes_section_and_current_values(self):
        p = ci.system_prompt({"slug": "farm-slurry", "keywords": "slurry, manure", "bogus": 1})
        self.assertIn("EDITING AN EXISTING DEFINITION", p)
        self.assertIn("farm-slurry", p)
        self.assertIn("slurry, manure", p)
        self.assertNotIn("bogus", p)                 # only known FIELD_KEYS are embedded

    def test_edit_greeting_names_category(self):
        self.assertIn("Farm slurry", ci.edit_greeting("Farm slurry"))


if __name__ == "__main__":
    unittest.main()
