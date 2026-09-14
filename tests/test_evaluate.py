"""AI evaluation tests (pure prompt/parse + a mocked run).
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
        self.assertIn("x" * 100, p)               # body present
        self.assertNotIn("x" * 101, p)            # ...truncated to body_limit

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


class TestStorageAndCandidates(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        for i, txt in enumerate(["slurry a", "slurry b", "slurry c"]):
            url = f"https://www.gov.uk/p{i}"
            self.conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, "
                              "search_text, title, description) VALUES (?, 'guidance',0,'h',?,?,?)",
                              (url, txt, f"Title {i}", f"Desc {i}"))
            self.conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, "
                              "organisation_slug, role) VALUES (?,?,?,?)",
                              (url, "environment-agency", "environment-agency", "primary"))
        self.conn.commit()
        self.flt = dict(organisations=["environment-agency"], document_types=["guidance"],
                        keywords=["slurry"], match="any")

    def tearDown(self):
        self.conn.close()

    def test_candidates_excludes_evaluated(self):
        first = evaluate.candidates(self.conn, 1, 2, **self.flt)
        self.assertEqual(len(first), 2)
        evaluate.save_result(self.conn, 1, first[0]["url"],
                             {"keep": 1, "score": 0.8, "reason": "ok"}, "m")
        remaining_urls = [r["url"] for r in evaluate.candidates(self.conn, 1, 10, **self.flt)]
        self.assertNotIn(first[0]["url"], remaining_urls)   # evaluated one dropped out
        self.assertEqual(len(remaining_urls), 2)

    def test_summary_and_results(self):
        evaluate.save_result(self.conn, 1, "https://www.gov.uk/p0", {"keep": 1, "score": 0.9, "reason": "a"}, "m")
        evaluate.save_result(self.conn, 1, "https://www.gov.uk/p1", {"keep": 0, "score": 0.2, "reason": "b"}, "m")
        s = evaluate.summary(self.conn, 1)
        self.assertEqual((s["evaluated"], s["kept"], s["dropped"]), (2, 1, 1))
        kept = evaluate.results(self.conn, 1, keep=1)
        self.assertEqual([r["url"] for r in kept], ["https://www.gov.uk/p0"])


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
        app._ai_config = lambda c: {"provider": "x", "label": "X", "base_url": "", "model": "m",
                                    "key": "k", "has_key": True, "price_in": 1.0, "price_out": 5.0}
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

    def test_run_stores_decisions_and_logs_spend(self):
        app, cid = self._app(n=3)
        result = app._run_evaluation(cid, 2)
        self.assertEqual(result["evaluated_this_run"], 2)
        self.assertEqual(result["kept"], 2)
        self.assertEqual(result["remaining"], 1)
        conn = app.connect()
        self.assertGreater(app._daily_spend(conn), 0)     # spend logged to ledger
        conn.close()

    def test_budget_stops_the_run(self):
        app, cid = self._app(n=5, cost=0.0002)
        self._set(app, "ai_daily_budget", "0.0003")        # < two calls' cost
        result = app._run_evaluation(cid, 5)
        self.assertEqual(result["stopped"], "budget")
        self.assertLessEqual(result["evaluated_this_run"], 2)

    def test_max_docs_caps_run(self):
        app, cid = self._app(n=5)
        self._set(app, "ai_max_docs_per_run", "1")
        result = app._run_evaluation(cid, 5)
        self.assertEqual(result["evaluated_this_run"], 1)   # capped to 1 despite asking for 5


if __name__ == "__main__":
    unittest.main()
