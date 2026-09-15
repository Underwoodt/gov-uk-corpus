"""Results-table endpoint resilience tests.
Run: python3 -m unittest -v tests.test_results_table"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import fastapi  # noqa: F401
    _HAS_WEBAPP = True
except Exception:
    _HAS_WEBAPP = False


@unittest.skipUnless(_HAS_WEBAPP, "web app deps (fastapi) not installed")
class TestResultsTable(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        os.environ["CORPUS_DB"] = self.path
        os.environ.pop("DB_HOST", None); os.environ["ADMIN_PASSWORD"] = ""
        import importlib
        import webapp.app as app
        importlib.reload(app)
        from govuk_corpus import categories as cat
        self.app = app
        conn = app.connect(); app.db.init_db(conn)
        for i in range(3):
            conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, search_text, title) "
                         "VALUES (?, 'guidance', 0, 'h', 'slurry', ?)", (f"https://www.gov.uk/p{i}", f"T{i}"))
            conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                         "VALUES (?,?,?,?)", (f"https://www.gov.uk/p{i}", "ea", "environment-agency", "primary"))
        self.cid = cat.create_category(conn, {"slug": "s", "owner_email": "a@b.co", "description": "d",
              "dept_slugs": "environment-agency", "document_type_slugs": "guidance", "keywords": "slurry"})
        conn.commit(); conn.close()
        from fastapi.testclient import TestClient
        self.c = TestClient(app.app)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_normal(self):
        j = self.c.get(f"/api/categories/{self.cid}/results-table").json()
        self.assertEqual(j["total"], 3)
        self.assertEqual(len(j["rows"]), 3)

    def test_slow_count_still_returns_rows(self):
        # A count that errors/times out must not sink the page: rows show, total null.
        self.app.cached_count = lambda conn, **k: (_ for _ in ()).throw(RuntimeError("count timeout"))
        j = self.c.get(f"/api/categories/{self.cid}/results-table").json()
        self.assertIsNone(j["total"])
        self.assertEqual(len(j["rows"]), 3)

    def test_rows_error_returns_json_not_plain_500(self):
        # A failing rows query returns a JSON error (so the UI shows the message,
        # not the generic "Could not load results").
        self.app.shortlist.export_rows = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("select timeout"))
        r = self.c.get(f"/api/categories/{self.cid}/results-table")
        self.assertEqual(r.status_code, 500)
        self.assertIn("select timeout", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
