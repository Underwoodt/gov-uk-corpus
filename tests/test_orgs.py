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

    # ---- page counts + hierarchy forest (for the org picker) ----------------

    def _add_pages(self, slug, n):
        for i in range(n):
            url = f"https://www.gov.uk/{slug}/{i}"
            self.conn.execute(
                "INSERT INTO content (url, is_redirect, content_hash) VALUES (?,?,?)", (url, 0, "h"))
            self.conn.execute(
                "INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                "VALUES (?,?,?,?)", (url, slug, slug, "primary"))
        self.conn.commit()

    def test_refresh_and_read_page_counts(self):
        self._add_pages("employment-tribunal", 5)
        self._add_pages("environment-agency", 3)
        n = orgs.refresh_page_counts(self.conn)
        self.assertEqual(n, 2)
        counts = orgs.page_counts(self.conn)
        self.assertEqual(counts["employment-tribunal"], 5)
        self.assertEqual(counts["environment-agency"], 3)
        self.assertTrue(orgs.counts_computed_at(self.conn))

    def test_hierarchy_forest_nesting_and_order(self):
        self._add_pages("environment-agency", 10)     # biggest root
        self._add_pages("ministry-of-justice", 4)
        self._add_pages("employment-tribunal", 2)     # nested two levels under MoJ
        orgs.refresh_page_counts(self.conn)
        forest = orgs.hierarchy_forest(self.conn)
        roots = [n["slug"] for n in forest]
        # Roots ordered by page count desc: environment-agency (10) before ministry-of-justice (4).
        self.assertEqual(roots, ["environment-agency", "ministry-of-justice"])
        moj = next(n for n in forest if n["slug"] == "ministry-of-justice")
        self.assertEqual([c["slug"] for c in moj["children"]], ["hm-courts-and-tribunals-service"])
        hmcts = moj["children"][0]
        self.assertEqual([c["slug"] for c in hmcts["children"]], ["employment-tribunal"])
        self.assertEqual(hmcts["children"][0]["pages"], 2)

    def test_hierarchy_forest_without_counts(self):
        # No refresh_page_counts run: every node has pages 0, ordered by title, still nested.
        forest = orgs.hierarchy_forest(self.conn)
        self.assertTrue(all(n["pages"] == 0 for n in forest))
        slugs = {n["slug"] for n in forest}
        self.assertIn("ministry-of-justice", slugs)
        self.assertNotIn("employment-tribunal", slugs)   # nested, not a root


if __name__ == "__main__":
    unittest.main()
