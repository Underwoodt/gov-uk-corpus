"""The "new shortlist" form must render without a saved category.

Regression test: form.html rendered the category tab bar unconditionally, and the
tab macro called url_for(..., cid=category.id) with category=None -> Starlette
raised "AssertionError: Must not be empty" and GET /categories/new 500'd.
Run: python3 -m unittest -v tests.test_new_category_page"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import fastapi  # noqa: F401
    _HAS_WEBAPP = True
except Exception:
    _HAS_WEBAPP = False


@unittest.skipUnless(_HAS_WEBAPP, "web app deps (fastapi) not installed")
class TestNewCategoryPage(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        os.environ["CORPUS_DB"] = self.path
        os.environ.pop("DB_HOST", None)
        os.environ.pop("DASHBOARD_PASSWORD", None)   # shared mode, no password -> authed
        import importlib
        import webapp.app as app
        importlib.reload(app)
        self.app = app
        conn = app.connect(); app.db.init_db(conn); conn.close()

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _client(self):
        from fastapi.testclient import TestClient
        c = TestClient(self.app.app, raise_server_exceptions=False)
        c.get("/login"); c.headers["X-CSRF-Token"] = c.cookies.get("sb_csrf")
        return c

    def test_get_new_renders_200(self):
        c = self._client()
        r = c.get("/categories/new")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Build a New Shortlist", r.text)

    def test_post_new_with_errors_rerenders_200(self):
        # Invalid submission re-renders the same form with category=None — must not 500.
        c = self._client()
        r = c.post("/categories/new", data={"slug": "", "keywords": ""})
        self.assertEqual(r.status_code, 200)
        self.assertIn("There is a problem", r.text)

    def test_edit_still_shows_tab_links(self):
        from govuk_corpus import categories as cat
        conn = self.app.connect()
        cid = cat.create_category(conn, {
            "slug": "tab-check", "owner_email": "t@example.gov",
            "dept_slugs": "", "document_type_slugs": "",
            "keywords": "slurry", "inclusion_context": "rules",
        })
        conn.close()
        c = self._client()
        r = c.get(f"/categories/{cid}/edit")
        self.assertEqual(r.status_code, 200)
        # Tab bar links need the saved cid.
        self.assertIn(f"/categories/{cid}/shortlist", r.text)
        self.assertIn("Filter Parameters", r.text)
