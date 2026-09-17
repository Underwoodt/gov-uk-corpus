"""CSRF double-submit guard tests (no DB needed — uses /logout, a POST route with
no auth/DB dependency). Run: python3 -m unittest -v tests.test_csrf"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CORPUS_DB", ":memory:")

from fastapi.testclient import TestClient
from webapp import app as A


class TestCSRF(unittest.TestCase):
    def _client(self):
        return TestClient(A.app)

    def test_get_issues_cookie(self):
        r = self._client().get("/login")
        self.assertTrue(r.cookies.get("sb_csrf"))

    def test_post_without_token_is_forbidden(self):
        # fresh client, no prior GET -> no cookie, no token
        r = self._client().post("/logout", follow_redirects=False)
        self.assertEqual(r.status_code, 403)

    def test_post_with_matching_header_passes(self):
        c = self._client()
        c.get("/login")                       # issues the sb_csrf cookie into the jar
        tok = c.cookies.get("sb_csrf")
        r = c.post("/logout", headers={"X-CSRF-Token": tok}, follow_redirects=False)
        self.assertNotEqual(r.status_code, 403)   # 303 redirect, not blocked

    def test_post_with_matching_form_field_passes(self):
        c = self._client()
        c.get("/login")
        tok = c.cookies.get("sb_csrf")
        r = c.post("/logout", data={"csrf": tok}, follow_redirects=False)
        self.assertNotEqual(r.status_code, 403)

    def test_post_with_wrong_token_is_forbidden(self):
        c = self._client()
        c.get("/login")
        r = c.post("/logout", headers={"X-CSRF-Token": "not-the-token"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)

    def test_safe_methods_pass(self):
        self.assertEqual(self._client().get("/login").status_code, 200)


if __name__ == "__main__":
    unittest.main()
