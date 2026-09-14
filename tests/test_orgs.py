"""Organisation registry + hierarchy tests (SQLite fixture).
Run: python3 -m unittest -v tests.test_orgs"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, orgs

# Minimal aggregate shaped like the GOV.UK search-API response.
FIXTURE = {"aggregates": {"organisations": {"options": [
    {"value": {"slug": "ministry-of-justice", "link": "/government/organisations/ministry-of-justice",
               "title": "Ministry of Justice", "acronym": "MoJ", "organisation_type": "ministerial_department",
               "organisation_state": "live",
               "child_organisations": ["hm-courts-and-tribunals-service"]}},
    {"value": {"slug": "hm-courts-and-tribunals-service", "title": "HMCTS",
               "parent_organisations": ["ministry-of-justice"],
               "child_organisations": ["employment-tribunal"]}},
    # employment-tribunal declares its parent only (edge must still be built)
    {"value": {"slug": "employment-tribunal", "title": "Employment Tribunal",
               "parent_organisations": ["hm-courts-and-tribunals-service"]}},
    # a standalone org via link only (no explicit slug)
    {"value": {"link": "/government/organisations/environment-agency", "title": "Environment Agency"}},
]}}}


class TestOrgs(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        fd, self.path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(FIXTURE, fh)
        self.counters = orgs.import_organisations(self.conn, self.path)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_import_counts(self):
        self.assertEqual(self.counters["organisations"], 4)
        self.assertGreaterEqual(self.counters["edges"], 2)

    def test_slug_from_link_fallback(self):
        self.assertEqual(orgs.get_org(self.conn, "environment-agency")["title"], "Environment Agency")

    def test_direct_children_and_parents(self):
        self.assertEqual(orgs.children(self.conn, "ministry-of-justice"),
                         ["hm-courts-and-tribunals-service"])
        self.assertEqual(orgs.parents(self.conn, "employment-tribunal"),
                         ["hm-courts-and-tribunals-service"])

    def test_edge_built_from_parent_side_only(self):
        # employment-tribunal only declared its parent; the HMCTS->ET edge must exist
        self.assertIn("employment-tribunal", orgs.children(self.conn, "hm-courts-and-tribunals-service"))

    def test_descendants_recursive(self):
        self.assertEqual(orgs.descendants(self.conn, "ministry-of-justice"),
                         ["employment-tribunal", "hm-courts-and-tribunals-service"])

    def test_expand_with_children(self):
        got = orgs.expand_with_children(self.conn, ["ministry-of-justice"])
        self.assertEqual(got, ["employment-tribunal", "hm-courts-and-tribunals-service",
                               "ministry-of-justice"])

    def test_reimport_is_idempotent(self):
        again = orgs.import_organisations(self.conn, self.path)
        self.assertEqual(again["organisations"], 4)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS n FROM organisations").fetchone()["n"], 4)


if __name__ == "__main__":
    unittest.main()
