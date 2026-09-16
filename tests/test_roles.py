"""System-role tests.
Run: python3 -m unittest -v tests.test_roles"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, roles


class TestNormalise(unittest.TestCase):
    def test_known_and_case_insensitive(self):
        self.assertEqual(roles.normalise_role("Administrator"), roles.ADMINISTRATOR)
        self.assertEqual(roles.normalise_role("  tester "), roles.TESTER)
        self.assertEqual(roles.normalise_role("USER"), roles.USER)

    def test_unknown_defaults(self):
        for v in ("", None, "root", "superuser"):
            self.assertEqual(roles.normalise_role(v), roles.DEFAULT_ROLE)


class TestAllows(unittest.TestCase):
    def test_unrestricted_open_to_all(self):
        for r in roles.ROLES:
            self.assertTrue(roles.allows(r, None))
            self.assertTrue(roles.allows(r, ""))

    def test_hierarchy(self):
        # Administrator ⊇ Tester ⊇ User
        self.assertTrue(roles.allows(roles.ADMINISTRATOR, roles.ADMINISTRATOR))
        self.assertTrue(roles.allows(roles.ADMINISTRATOR, roles.TESTER))
        self.assertTrue(roles.allows(roles.TESTER, roles.TESTER))
        self.assertFalse(roles.allows(roles.TESTER, roles.ADMINISTRATOR))
        self.assertFalse(roles.allows(roles.USER, roles.TESTER))
        self.assertFalse(roles.allows(roles.USER, roles.ADMINISTRATOR))


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_default_then_roundtrip(self):
        self.assertEqual(roles.get_role(self.conn), roles.DEFAULT_ROLE)
        roles.set_role(self.conn, "Tester")
        self.assertEqual(roles.get_role(self.conn), roles.TESTER)
        # An invalid value stored via set_role is coerced to the default.
        roles.set_role(self.conn, "nonsense")
        self.assertEqual(roles.get_role(self.conn), roles.DEFAULT_ROLE)


if __name__ == "__main__":
    unittest.main()
