"""AI evaluation tests: pure prompt/parse, run tracking, and a mocked run.
Run: python3 -m unittest -v tests.test_evaluate"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, evaluate

try:
    import fastapi  # noqa: F401
    _HAS_WEBAPP = True
except Exception:
    _HAS_WEBAPP = False


class TestPromptAndParse(unittest.TestCase):
    def test_prompt_contains_context_and_truncates_body(self):
        p = evaluate.build_prompt("slurry storage", "sewage sludge", "Title", "Desc",
                                  "x" * 10000, body_limit=100)
        self.assertIn("slurry storage", p)
        self.assertIn("sewage sludge", p)
        self.assertIn("Title", p)
        self.assertIn("x" * 100, p)
        self.assertNotIn("x" * 101, p)

    def test_parse_plain_json(self):
        d = evaluate.parse_decision('{"keep": true, "score": 0.9, "reason": "on topic"}')
        self.assertEqual(d, {"keep": 1, "score": 0.9, "reason": "on topic"})

    def test_parse_code_fence_and_prose(self):
        d = evaluate.parse_decision('Sure!\n```json\n{"keep": false, "score": 0.1, "reason": "off"}\n```')
        self.assertEqual(d["keep"], 0)
        self.assertEqual(d["score"], 0.1)

    def test_parse_unparseable(self):
        self.assertIsNone(evaluate.parse_decision("not json at all"))
        self.assertIsNone(evaluate.parse_decision(""))


class TestRuns(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        for i in range(3):
            url = f"https://www.gov.uk/p{i}"
            self.conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, "
                              "search_text, title, description) VALUES (?, 'guidance',0,'h',?,?,?)",
                              (url, "slurry", f"Title {i}", f"Desc {i}"))
            self.conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, "
                              "organisation_slug, role) VALUES (?,?,?,?)",
                              (url, "environment-agency", "environment-agency", "primary"))
        self.conn.commit()
        self.flt = dict(organisations=["environment-agency"], document_types=["guidance"],
                        keywords=["slurry"], match="any")

    def tearDown(self):
        self.conn.close()

    def test_candidates_are_per_run(self):
        run_a = evaluate.create_run(self.conn, 1, "model-a", "anthropic")
        run_b = evaluate.create_run(self.conn, 1, "model-b", "deepseek")
        first = evaluate.run_candidates(self.conn, run_a, 1, 2, **self.flt)
        self.assertEqual(len(first), 2)
        evaluate.save_page(self.conn, run_a, 1, first[0]["url"], {"keep": 1, "score": 0.8, "reason": "ok"}, 120)
        # run_a no longer offers that page...
        self.assertNotIn(first[0]["url"],
                         [r["url"] for r in evaluate.run_candidates(self.conn, run_a, 1, 10, **self.flt)])
        # ...but run_b still sees ALL pages (per-run history)
        self.assertEqual(len(evaluate.run_candidates(self.conn, run_b, 1, 10, **self.flt)), 3)

    def test_run_totals_update(self):
        run = evaluate.create_run(self.conn, 1, "m", "anthropic")
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p0", {"keep": 1, "score": 0.9, "reason": "a"}, 100)
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p1", {"keep": 0, "score": 0.2, "reason": "b"}, 300)
        evaluate.add_run_cost(self.conn, run, 0.0025)
        r = evaluate.get_run(self.conn, run)
        self.assertEqual((r["pages"], r["kept"], r["dropped"], r["total_ms"]), (2, 1, 1, 400))
        self.assertAlmostEqual(r["cost"], 0.0025, places=6)

    def test_list_runs_and_results_filter(self):
        run = evaluate.create_run(self.conn, 1, "m", "anthropic")
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p0", {"keep": 1, "score": 0.9, "reason": "a"}, 10)
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p1", {"keep": 0, "score": 0.2, "reason": "b"}, 10)
        self.assertEqual(len(evaluate.list_runs(self.conn, 1)), 1)
        keep = evaluate.run_results(self.conn, run, keep=1)
        self.assertEqual([r["url"] for r in keep], ["https://www.gov.uk/p0"])


@unittest.skipUnless(_HAS_WEBAPP, "web app deps (fastapi) not installed")
class TestRunEvaluationMocked(unittest.TestCase):
    def _app(self, n=5, cost=0.0002):
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["CORPUS_DB"] = self.path
        os.environ.pop("DB_HOST", None)
        import importlib
        import webapp.app as app
        importlib.reload(app)
        from govuk_corpus import categories as cat
        conn = app.connect()
        app.db.init_db(conn)
        for i in range(n):
            url = f"https://www.gov.uk/p{i}"
            conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, search_text, title) "
                         "VALUES (?, 'guidance',0,'h',?,?)", (url, "slurry", f"Title {i}"))
            conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                         "VALUES (?,?,?,?)", (url, "environment-agency", "environment-agency", "primary"))
        cid = cat.create_category(conn, {"slug": "demo", "owner_email": "a@b.co", "description": "d",
              "dept_slugs": "environment-agency", "document_type_slugs": "guidance",
              "keywords": "slurry", "inclusion_context": "slurry"})
        conn.commit()
        conn.close()
        app._ai_config = lambda c: {"provider": "anthropic", "label": "Anthropic", "base_url": "",
                                    "model": "m", "key": "k", "has_key": True, "price_in": 1.0, "price_out": 5.0}
        app._provider_key = lambda p: "k"
        app._ai_reply = lambda cfg, system, prompt: {
            "reply": '{"keep": true, "score": 0.8, "reason": "relevant"}',
            "input_tokens": 100, "output_tokens": 20, "cost_usd": cost}
        return app, cid

    def tearDown(self):
        if getattr(self, "path", None) and os.path.exists(self.path):
            os.unlink(self.path)

    def _set(self, app, key, value):
        conn = app.connect()
        from govuk_corpus import settings as st
        st.set_setting(conn, key, value)
        conn.close()

    def test_run_created_and_results_stored(self):
        app, cid = self._app(n=3)
        result = app._run_evaluation(cid, 2)
        self.assertEqual(result["evaluated_this_run"], 2)
        self.assertTrue(result["run_id"])
        conn = app.connect()
        run = evaluate.get_run(conn, result["run_id"])
        self.assertEqual(run["pages"], 2)
        self.assertEqual(run["kept"], 2)
        self.assertGreater(app._daily_spend(conn), 0)
        conn.close()

    def test_new_run_reevaluates_same_pages(self):
        app, cid = self._app(n=3)
        r1 = app._run_evaluation(cid, 3)
        conn = app.connect()
        # start a new run (fresh) -> active run changes
        run2 = evaluate.create_run(conn, cid, "m2", "anthropic")
        from govuk_corpus import settings as st
        st.set_setting(conn, f"active_run_{cid}", run2)
        conn.close()
        r2 = app._run_evaluation(cid, 3)
        self.assertNotEqual(r1["run_id"], r2["run_id"])
        self.assertEqual(r2["evaluated_this_run"], 3)   # re-evaluated the same 3 pages in the new run
        conn = app.connect()
        self.assertEqual(len(evaluate.list_runs(conn, cid)), 2)
        conn.close()

    def test_budget_stops_and_max_docs_caps(self):
        app, cid = self._app(n=5, cost=0.0002)
        self._set(app, "ai_daily_budget", "0.0003")
        result = app._run_evaluation(cid, 5)
        self.assertEqual(result["stopped"], "budget")
        self.assertLessEqual(result["evaluated_this_run"], 2)


if __name__ == "__main__":
    unittest.main()
