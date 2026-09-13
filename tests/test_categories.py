"""Category (saved shortlist spec) CRUD + validation tests.
Run: python3 -m unittest -v tests.test_categories"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import categories as cat
from govuk_corpus import db


def _valid_data(**over):
    d = {
        "owner_email": "a@b.gov.uk",
        "description": "Slurry docs",
        "dept_slugs": "environment-agency, defra",
        "document_type_slugs": "guidance, detailed_guide",
        "keywords": "slurry\nnitrate",
        "inclusion_context": "storage and spreading rules",
    }
    d.update(over)
    return d


class TestValidation(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(cat.validate(_valid_data()), [])

    def test_required_missing(self):
        errs = cat.validate(_valid_data(owner_email="", dept_slugs=""))
        self.assertTrue(any("Owner Email" in e for e in errs))
        self.assertTrue(any("Departments" in e for e in errs))

    def test_bad_email(self):
        errs = cat.validate(_valid_data(owner_email="not-an-email"))
        self.assertTrue(any("valid email" in e for e in errs))

    def test_description_too_long(self):
        errs = cat.validate(_valid_data(description="x" * 101))
        self.assertTrue(any("Description must be 100" in e for e in errs))

    def test_keyword_line_too_many_words(self):
        errs = cat.validate(_valid_data(keywords="slurry storage rules"))
        self.assertTrue(any("more than 2 words" in e for e in errs))

    def test_parse_list(self):
        self.assertEqual(cat.parse_list("a, b\n c ,,"), ["a", "b", "c"])


class TestCrud(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_create_get_update_delete(self):
        cid = cat.create_category(self.conn, _valid_data())
        self.assertIsInstance(cid, int)
        got = cat.get_category(self.conn, cid)
        self.assertEqual(got["description"], "Slurry docs")
        self.assertEqual(got["status"], "draft")
        self.assertEqual(got["only_use_extra_guidance_urls"], 0)

        cat.update_category(self.conn, cid, _valid_data(description="Updated"), status="published")
        got = cat.get_category(self.conn, cid)
        self.assertEqual(got["description"], "Updated")
        self.assertEqual(got["status"], "published")

        rows = cat.list_categories(self.conn)
        self.assertEqual(len(rows), 1)

        cat.delete_category(self.conn, cid)
        self.assertIsNone(cat.get_category(self.conn, cid))

    def test_boolean_coercion(self):
        cid = cat.create_category(self.conn, _valid_data(only_use_extra_law_urls="Y"))
        self.assertEqual(cat.get_category(self.conn, cid)["only_use_extra_law_urls"], 1)


if __name__ == "__main__":
    unittest.main()
