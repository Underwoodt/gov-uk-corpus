"""Creating a category takes the owner email from the logged-in user.
Run: python3 -m unittest -v tests.test_owner_email"""
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
class TestOwnerEmailFromUser(unittest.TestCase):
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

    def test_create_uses_logged_in_user_email(self):
        from fastapi.testclient import TestClient
        from govuk_corpus import categories as cat
        app = self.app
        # Pretend someone is logged in (accounts mode is exercised via current_user).
        app.current_user = lambda request: {"email": "owner@defra.gov.uk", "role": "User"}
        c = TestClient(app.app)
        c.get("/login"); c.headers["X-CSRF-Token"] = c.cookies.get("sb_csrf")   # CSRF double-submit
        r = c.post("/categories/new", data={
            "slug": "my-cat",
            "owner_email": "someone-else@example.gov",   # must be ignored
            "dept_slugs": "environment-agency",
            "document_type_slugs": "guidance",
            "keywords": "slurry",
            "inclusion_context": "storage rules",
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 303)             # created -> redirect to its page
        conn = app.connect()
        rows = cat.list_categories(conn); conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner_email"], "owner@defra.gov.uk")   # from the user, not the form

    def test_shared_mode_keeps_submitted_email(self):
        from fastapi.testclient import TestClient
        from govuk_corpus import categories as cat
        app = self.app                                    # current_user returns None (shared mode)
        c = TestClient(app.app)
        c.get("/login"); c.headers["X-CSRF-Token"] = c.cookies.get("sb_csrf")
        r = c.post("/categories/new", data={
            "slug": "shared-cat", "owner_email": "typed@example.gov",
            "dept_slugs": "environment-agency", "document_type_slugs": "guidance",
            "keywords": "slurry", "inclusion_context": "rules",
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        conn = app.connect()
        rows = cat.list_categories(conn); conn.close()
        self.assertEqual(rows[0]["owner_email"], "typed@example.gov")    # no user -> use the form
