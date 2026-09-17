"""Category-assistant interview parsing + endpoint tests.
Run: python3 -m unittest -v tests.test_category_interview"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import category_interview as ci

try:
    import fastapi  # noqa: F401
    _HAS_WEBAPP = True
except Exception:
    _HAS_WEBAPP = False


class TestParseFields(unittest.TestCase):
    def test_extracts_fenced_json(self):
        reply = ('Great, here is a draft.\n\n```json\n{"slug": "farm-slurry", '
                 '"dept_slugs": "environment-agency", "include_child_orgs": true, '
                 '"keywords": "slurry, animal manure", "inclusion_context": "About slurry."}\n```\n'
                 "It looks solid; double-check the org list.")
        f = ci.parse_fields(reply)
        self.assertEqual(f["slug"], "farm-slurry")
        self.assertEqual(f["dept_slugs"], "environment-agency")
        self.assertTrue(f["include_child_orgs"])
        self.assertEqual(f["keywords"], "slurry, animal manure")
        self.assertNotIn("owner_email", f)   # omitted keys stay omitted

    def test_none_before_draft(self):
        self.assertIsNone(ci.parse_fields("What organisations publish this?"))
        self.assertIsNone(ci.parse_fields(""))

    def test_ignores_unknown_keys(self):
        f = ci.parse_fields('```json\n{"slug": "x", "dept_slugs": "ea", "bogus": 1}\n```')
        self.assertEqual(set(f), {"slug", "dept_slugs"})


@unittest.skipUnless(_HAS_WEBAPP, "web app deps (fastapi) not installed")
class TestAssistantEndpoint(unittest.TestCase):
    def _client(self):
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        os.environ["CORPUS_DB"] = self.path
        os.environ.pop("DB_HOST", None); os.environ["ADMIN_PASSWORD"] = ""
        import importlib
        import webapp.app as app
        importlib.reload(app)
        conn = app.connect(); app.db.init_db(conn); conn.close()
        app._ai_config = lambda c: {"provider": "anthropic", "label": "A", "base_url": "",
                                    "model": "m", "key": "k", "has_key": True,
                                    "price_in": 1.0, "price_out": 5.0}
        from fastapi.testclient import TestClient
        c = TestClient(app.app)
        c.get("/login"); c.headers["X-CSRF-Token"] = c.cookies.get("sb_csrf")   # CSRF double-submit
        return app, c

    def tearDown(self):
        if getattr(self, "path", None) and os.path.exists(self.path):
            os.unlink(self.path)

    def test_returns_reply_and_fields_when_drafted(self):
        app, c = self._client()
        app._ai_chat = lambda cfg, system, msgs, **kw: {
            "reply": 'Here is your draft.\n```json\n{"slug":"s","dept_slugs":"ea"}\n```',
            "cost_usd": 0.0001, "input_tokens": 50, "output_tokens": 20}
        r = c.post("/api/categories/assistant", json={"messages": [{"role": "user", "content": "farms"}]})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertIn("draft", j["reply"])
        self.assertEqual(j["fields"], {"slug": "s", "dept_slugs": "ea"})

    def test_no_fields_mid_interview(self):
        app, c = self._client()
        app._ai_chat = lambda cfg, system, msgs, **kw: {
            "reply": "Which organisations publish this?", "cost_usd": 0.0, "input_tokens": 10, "output_tokens": 5}
        r = c.post("/api/categories/assistant", json={"messages": [{"role": "user", "content": "hi"}]})
        self.assertIsNone(r.json()["fields"])


if __name__ == "__main__":
    unittest.main()
