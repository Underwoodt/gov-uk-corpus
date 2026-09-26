"""Download page (guc-0008) and the export it submits: run pre-selection, the always-on Run ID
column, and several runs in one file. Run: python3 -m unittest -v tests.test_download"""
from __future__ import annotations

import csv
import io
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
class TestDownloadRuns(unittest.TestCase):
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
        for slug in ("p0", "p1"):
            url = f"https://www.gov.uk/{slug}"
            conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, search_text, title, content_id) "
                         "VALUES (?, 'guidance',0,?,?,?,?)", (url, f"h-{slug}", "slurry", f"Title {slug}", f"c-{slug}"))
            conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                         "VALUES (?,?,?,?)", (url, "environment-agency", "environment-agency", "primary"))
        from govuk_corpus import categories as cat
        self.cid = cat.create_category(conn, {"slug": "demo", "owner_email": "a@b.co", "description": "Demo",
                                              "dept_slugs": "environment-agency", "document_type_slugs": "guidance",
                                              "keywords": "slurry", "inclusion_context": "slurry"})
        # Two inclusion runs that disagree about p1, so the rows differ per run.
        self.r1 = evaluate.create_run(conn, self.cid, "m", "anthropic")
        evaluate.save_page(conn, self.r1, self.cid, "https://www.gov.uk/p0", {"keep": 1, "score": 0.9, "reason": "yes"}, 5)
        evaluate.save_page(conn, self.r1, self.cid, "https://www.gov.uk/p1", {"keep": 0, "score": 0.0, "reason": "no"}, 5)
        self.r2 = evaluate.create_run(conn, self.cid, "m", "anthropic")
        evaluate.save_page(conn, self.r2, self.cid, "https://www.gov.uk/p0", {"keep": 1, "score": 0.8, "reason": "still yes"}, 5)
        evaluate.save_page(conn, self.r2, self.cid, "https://www.gov.uk/p1", {"keep": 1, "score": 0.6, "reason": "now yes"}, 5)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ.pop("CORPUS_DB", None)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _client(self):
        from fastapi.testclient import TestClient
        return TestClient(self.app.app)

    def _csv(self, resp):
        rows = list(csv.reader(io.StringIO(resp.text)))
        return rows[0], rows[1:]

    def test_run_parameter_preselects_and_picker_is_multi(self):
        c = self._client()
        r = c.get(f"/categories/{self.cid}/download", params={"run": self.r1, "stage": "final"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<select id=\"dl-run\"", r.text)                      # checkboxes, not a select
        self.assertLess(r.text.index('id="filename"'), r.text.index('<label class="q">Runs'))   # runs sit last, by the button
        self.assertLess(r.text.index('<label class="q">Runs'), r.text.index('type="submit">Download'))
        self.assertIn(f'name="run" value="{self.r1}" checked', r.text)
        self.assertIn(f'name="run" value="{self.r2}" >', r.text)
        r = c.get(f"/categories/{self.cid}/download", params=[("run", self.r1), ("run", self.r2)])
        self.assertIn(f'name="run" value="{self.r1}" checked', r.text)
        self.assertIn(f'name="run" value="{self.r2}" checked', r.text)
        self.assertIn('value="run_id" checked disabled', r.text)        # Run ID always on, like URL

    def test_picker_lists_only_this_categorys_runs(self):
        conn = self.app.connect()
        from govuk_corpus import categories as cat
        other = cat.create_category(conn, {"slug": "other", "owner_email": "a@b.co", "description": "Other",
                                           "dept_slugs": "environment-agency", "document_type_slugs": "guidance",
                                           "keywords": "slurry", "inclusion_context": "slurry"})
        foreign = evaluate.create_run(conn, other, "m", "anthropic")
        conn.commit(); conn.close()
        r = self._client().get(f"/categories/{self.cid}/download")
        self.assertIn(f'value="{self.r1}"', r.text)
        self.assertNotIn(foreign, r.text)                                        # another shortlist's run is not offered
        # ...and cannot be smuggled in through the URL either
        header, rows = self._csv(self._client().get(f"/categories/{self.cid}/export",
                                                    params=[("stage", "final"), ("format", "csv"), ("run", foreign)]))
        self.assertTrue(rows and all(row[1] == self.r2 for row in rows))         # falls back to this category's active run

    def test_export_always_carries_run_id(self):
        c = self._client()
        header, rows = self._csv(c.get(f"/categories/{self.cid}/export",
                                       params={"stage": "final", "fields": "title", "format": "csv"}))
        self.assertEqual(header[:2], ["URL", "Run ID"])
        self.assertTrue(rows and all(row[1] == self.r2 for row in rows))    # no run given -> the active (latest) run

    def test_several_runs_in_one_file(self):
        c = self._client()
        header, rows = self._csv(c.get(f"/categories/{self.cid}/export",
                                       params=[("stage", "final"), ("fields", "inclusion_reason"), ("format", "csv"),
                                               ("run", self.r1), ("run", self.r2), ("run", "not-a-run")]))
        self.assertEqual(header, ["URL", "Run ID", "Inclusion reason"])
        by_run = {}
        for url, rid, reason in rows:
            by_run.setdefault(rid, {})[url] = reason
        self.assertEqual(set(by_run), {self.r1, self.r2})                   # the bogus id is ignored
        self.assertEqual(sorted(by_run[self.r1]), ["https://www.gov.uk/p0"])           # run 1 kept only p0
        self.assertEqual(sorted(by_run[self.r2]), ["https://www.gov.uk/p0", "https://www.gov.uk/p1"])
        self.assertEqual(by_run[self.r2]["https://www.gov.uk/p1"], "now yes")
        j = c.get(f"/categories/{self.cid}/export", params=[("stage", "final"), ("format", "json"),
                                                            ("run", self.r1), ("run", self.r2)]).json()
        self.assertEqual({row["run_id"] for row in j}, {self.r1, self.r2})


if __name__ == "__main__":
    unittest.main()
