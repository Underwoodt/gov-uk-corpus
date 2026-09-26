"""Data Analysis sub-tab (guc-0004c4) and the review list (guc-0032).
Run: python3 -m unittest -v tests.test_analysis"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import evaluate

try:
    import fastapi  # noqa: F401
    _HAS_WEBAPP = True
except Exception:
    _HAS_WEBAPP = False


@unittest.skipUnless(_HAS_WEBAPP, "fastapi not installed")
class TestAnalysis(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["CORPUS_DB"] = self.path
        os.environ.pop("DB_HOST", None)
        os.environ["DASHBOARD_PASSWORD"] = ""
        os.environ["AUTH_MODE"] = "shared"
        import importlib
        import webapp.app as app
        importlib.reload(app)
        self.app = app
        conn = app.connect()
        app.db.init_db(conn)
        for slug in ("p0", "p1", "p2", "p3", "p4"):
            url = f"https://www.gov.uk/{slug}"
            conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, search_text, title, content_id) "
                         "VALUES (?, 'guidance',0,?,?,?,?)", (url, f"h-{slug}", "slurry", f"Title {slug}", f"c-{slug}"))
            conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                         "VALUES (?,?,?,?)", (url, "environment-agency", "environment-agency", "primary"))
        from govuk_corpus import categories as cat
        self.cid = cat.create_category(conn, {"slug": "demo", "owner_email": "a@b.co", "description": "Demo",
                                              "dept_slugs": "environment-agency", "document_type_slugs": "guidance",
                                              "keywords": "slurry", "inclusion_context": "slurry"})
        U = lambda s: f"https://www.gov.uk/{s}"
        # Baseline: p0 keep, p1 keep, p2 reject, p3 unscored, p4 keep (p4 only in baseline)
        self.base = evaluate.create_run(conn, self.cid, "m", "anthropic")
        evaluate.save_page(conn, self.base, self.cid, U("p0"), {"keep": 1, "score": 0.9, "reason": "core"}, 5)
        evaluate.save_page(conn, self.base, self.cid, U("p1"), {"keep": 1, "score": 0.7, "reason": "some"}, 5)
        evaluate.save_page(conn, self.base, self.cid, U("p2"), {"keep": 0, "score": 0.1, "reason": "no"}, 5)
        evaluate.save_page(conn, self.base, self.cid, U("p3"), None, 5)
        evaluate.save_page(conn, self.base, self.cid, U("p4"), {"keep": 1, "score": 0.8, "reason": "only base"}, 5)
        # Comparison: p0 keep, p1 REJECT, p2 KEEP, p3 keep (was unscored); p4 absent
        self.comp = evaluate.create_run(conn, self.cid, "m", "anthropic")
        evaluate.save_page(conn, self.comp, self.cid, U("p0"), {"keep": 1, "score": 0.9, "reason": "core"}, 5)
        evaluate.save_page(conn, self.comp, self.cid, U("p1"), {"keep": 0, "score": 0.2, "reason": "homonym"}, 5)
        evaluate.save_page(conn, self.comp, self.cid, U("p2"), {"keep": 1, "score": 0.6, "reason": "now relevant"}, 5)
        evaluate.save_page(conn, self.comp, self.cid, U("p3"), {"keep": 1, "score": 0.5, "reason": "scored now"}, 5)
        # An exclusion run on the comparison: must not be offered as a baseline/comparison.
        self.excl = evaluate.create_run(conn, self.cid, "m", "anthropic", phase=evaluate.PHASE_EXCLUSION, source_run_id=self.comp)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ.pop("CORPUS_DB", None)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _client(self):
        from fastapi.testclient import TestClient
        return TestClient(self.app.app)

    def test_tab_and_page_render(self):
        c = self._client()
        r = c.get(f"/categories/{self.cid}/analysis")
        self.assertEqual(r.status_code, 200)
        self.assertIn("guc-0004c4", r.text)
        self.assertIn("Phase 1 – Inclusion (Recall): accepted vs rejected", r.text)
        self.assertIn('data-tabid="guc-0004c4"', c.get(f"/categories/{self.cid}/performance").text)   # linked from Run performance

    def test_phase1_compare_counts_only_shared_pages(self):
        c = self._client()
        j = c.get(f"/api/categories/{self.cid}/analysis/phase1", params={"baseline": self.base, "comparison": self.comp}).json()
        self.assertEqual((j["shared"], j["only_baseline"], j["only_comparison"]), (4, 1, 0))     # p4 is left out
        self.assertEqual({k: j["baseline"][k] for k in ("accepted", "rejected", "unscored", "total")},
                         {"accepted": 2, "rejected": 1, "unscored": 1, "total": 4})
        self.assertEqual({k: j["comparison"][k] for k in ("accepted", "rejected", "unscored", "total")},
                         {"accepted": 3, "rejected": 1, "unscored": 0, "total": 4})
        self.assertEqual(j["differences"], {"total": 3, "accepted_to_rejected": 1, "rejected_to_accepted": 1, "involving_unscored": 1})
        self.assertIn("Run", j["baseline"]["label"])
        # exclusion runs and foreign ids are refused
        self.assertEqual(c.get(f"/api/categories/{self.cid}/analysis/phase1", params={"baseline": self.base, "comparison": self.excl}).status_code, 400)
        self.assertEqual(c.get(f"/api/categories/{self.cid}/analysis/phase1", params={"baseline": self.base, "comparison": "nope"}).status_code, 400)

    def test_review_list_titles_and_rows(self):
        c = self._client()
        r = c.get(f"/categories/{self.cid}/analysis/list", params={"context": "phase1", "run": self.comp, "verdict": "keep", "shared_with": self.base})
        self.assertEqual(r.status_code, 200)
        self.assertIn("<h1 style=\"margin:18px 0 4px;\">Inclusion Phase (Recall) – Keep</h1>", r.text)
        self.assertIn("guc-0032", r.text)
        for slug in ("p0", "p2", "p3"):
            self.assertIn(f"Title {slug}", r.text)
        self.assertNotIn("Title p1", r.text)                                   # rejected by the comparison
        r = c.get(f"/categories/{self.cid}/analysis/list", params={"context": "phase1", "run": self.base, "verdict": "all"})
        self.assertIn("Inclusion Phase (Recall) – All", r.text)
        self.assertIn("Title p4", r.text)                                      # no shared_with: the whole run
        r = c.get(f"/categories/{self.cid}/analysis/list", params={"context": "phase1", "run": self.base, "verdict": "reject"})
        self.assertIn("Inclusion Phase (Recall) – Reject", r.text)
        self.assertIn("Title p2", r.text)
        self.assertNotIn("Title p0", r.text)

    def test_review_list_differences(self):
        c = self._client()
        r = c.get(f"/categories/{self.cid}/analysis/list", params={"context": "phase1_diff", "baseline": self.base, "comparison": self.comp})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Inclusion Phase (Recall) – Differences", r.text)
        for slug in ("p1", "p2", "p3"):
            self.assertIn(f"Title {slug}", r.text)
        self.assertNotIn("Title p0", r.text)                                   # same decision in both
        self.assertNotIn("Title p4", r.text)                                   # not in both runs
        self.assertIn("<th>Baseline decision</th>", r.text)
        self.assertIn("homonym", r.text)                                       # the comparison's reason is shown


if __name__ == "__main__":
    unittest.main()
