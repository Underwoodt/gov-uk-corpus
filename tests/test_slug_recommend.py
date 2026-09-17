"""Slug matching used to recommend real slugs in the category chat.
Run: python3 -m unittest -v tests.test_slug_recommend"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, orgs
from govuk_corpus import category_interview as ci


class TestOrgSearch(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        for slug, title, acr in [
            ("environment-agency", "Environment Agency", "EA"),
            ("department-for-environment-food-rural-affairs",
             "Department for Environment, Food & Rural Affairs", "Defra"),
            ("animal-and-plant-health-agency", "Animal and Plant Health Agency", "APHA"),
            ("natural-england", "Natural England", None),
        ]:
            self.conn.execute("INSERT INTO organisations (slug, title, acronym) VALUES (?,?,?)",
                              (slug, title, acr))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_acronym_maps_to_slug(self):
        slugs = [m["slug"] for m in orgs.search(self.conn, "I want DEFRA and APHA pages")]
        self.assertIn("department-for-environment-food-rural-affairs", slugs)
        self.assertIn("animal-and-plant-health-agency", slugs)

    def test_plain_name_substring(self):
        slugs = [m["slug"] for m in orgs.search(self.conn, "the environment agency guidance")]
        self.assertIn("environment-agency", slugs)
        # 'environment' also appears in the Defra title -> recommended widely.
        self.assertIn("department-for-environment-food-rural-affairs", slugs)

    def test_exact_slug_matches_itself(self):
        slugs = [m["slug"] for m in orgs.search(self.conn, "natural-england")]
        self.assertEqual(slugs, ["natural-england"])

    def test_stopwords_and_no_match_return_empty(self):
        self.assertEqual(orgs.search(self.conn, "the and for of"), [])
        self.assertEqual(orgs.search(self.conn, "zzzznope"), [])


class TestDocTypeMatch(unittest.TestCase):
    def test_loose_names_map_to_slugs(self):
        got = set(ci.match_document_types("I want guidance, forms and news"))
        self.assertIn("guidance", got)
        self.assertIn("form", got)          # 'forms' -> form
        self.assertIn("news_story", got)    # 'news' -> news_story

    def test_exact_and_none(self):
        self.assertIn("policy_paper", ci.match_document_types("policy_paper please"))
        self.assertEqual(ci.match_document_types("just some prose"), [])
