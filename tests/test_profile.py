"""Profile page (UI display level) smoke test.
Run: python3 -m unittest -v tests.test_profile"""
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
class TestProfile(unittest.TestCase):
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

    def test_profile_page_lists_all_levels(self):
        from fastapi.testclient import TestClient
        r = TestClient(self.app.app).get("/profile")
        self.assertEqual(r.status_code, 200)
        for level in ("Simple", "Advanced", "Expert", "Admin"):
            self.assertIn(level, r.text)
        self.assertIn("ui_level", r.text)      # the localStorage key / radio name
        self.assertIn("guc-0016", r.text)      # page id
