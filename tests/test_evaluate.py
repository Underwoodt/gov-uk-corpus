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


class TestPhaseMode(unittest.TestCase):
    def test_normalise_mode(self):
        self.assertEqual(evaluate.normalise_mode("batch"), evaluate.MODE_BATCH)
        self.assertEqual(evaluate.normalise_mode("  BATCH "), evaluate.MODE_BATCH)
        self.assertEqual(evaluate.normalise_mode("synchronous"), evaluate.MODE_SYNC)
        # Anything unrecognised / empty / None defaults to synchronous.
        for v in ("", None, "async", "sync", "on"):
            self.assertEqual(evaluate.normalise_mode(v), evaluate.MODE_SYNC)


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
        evaluate.add_run_cost(self.conn, run, 0.0025, 1200, 60, hit_tokens=200, miss_tokens=1000)
        evaluate.add_run_cost(self.conn, run, 0.0015, 800, 40, hit_tokens=300, miss_tokens=500)
        r = evaluate.get_run(self.conn, run)
        self.assertEqual((r["pages"], r["kept"], r["dropped"], r["total_ms"]), (2, 1, 1, 400))
        self.assertAlmostEqual(r["cost"], 0.0040, places=6)
        self.assertEqual((r["in_tokens"], r["out_tokens"]), (2000, 100))
        self.assertEqual((r["hit_tokens"], r["miss_tokens"]), (500, 1500))

    def test_default_name_is_sequential_per_category(self):
        r1 = evaluate.create_run(self.conn, 1, "m", "anthropic")
        r2 = evaluate.create_run(self.conn, 1, "m", "anthropic")
        r_other = evaluate.create_run(self.conn, 2, "m", "anthropic")   # different category
        self.assertEqual(evaluate.get_run(self.conn, r1)["name"], "Test-1")
        self.assertEqual(evaluate.get_run(self.conn, r2)["name"], "Test-2")
        self.assertEqual(evaluate.get_run(self.conn, r_other)["name"], "Test-1")

    def test_rename_run(self):
        r = evaluate.create_run(self.conn, 1, "m", "anthropic")
        evaluate.rename_run(self.conn, r, "  Slurry baseline  ")
        self.assertEqual(evaluate.get_run(self.conn, r)["name"], "Slurry baseline")

    def test_run_chain(self):
        inc = evaluate.create_run(self.conn, 1, "m", "anthropic", phase=evaluate.PHASE_INCLUSION)
        exc = evaluate.create_run(self.conn, 1, "m2", "deepseek",
                                  phase=evaluate.PHASE_EXCLUSION, source_run_id=inc)
        other = evaluate.create_run(self.conn, 1, "m3", "anthropic", phase=evaluate.PHASE_INCLUSION)
        # The chain is the same from either end, oldest-first, and excludes the unrelated run.
        self.assertEqual([r["run_id"] for r in evaluate.run_chain(self.conn, exc)], [inc, exc])
        self.assertEqual([r["run_id"] for r in evaluate.run_chain(self.conn, inc)], [inc, exc])
        self.assertNotIn(other, [r["run_id"] for r in evaluate.run_chain(self.conn, exc)])

    def test_explicit_name_overrides_default(self):
        r = evaluate.create_run(self.conn, 1, "m", "anthropic", name="Haiku run")
        self.assertEqual(evaluate.get_run(self.conn, r)["name"], "Haiku run")

    def test_delete_run_removes_run_and_results(self):
        run = evaluate.create_run(self.conn, 1, "m", "anthropic")
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p0", {"keep": 1, "score": .9, "reason": "a"}, 10)
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p1", {"keep": 0, "score": .2, "reason": "b"}, 10)
        self.assertIsNotNone(evaluate.get_run(self.conn, run))
        self.assertEqual(len(evaluate.run_results(self.conn, run)), 2)
        evaluate.delete_run(self.conn, run)
        self.assertIsNone(evaluate.get_run(self.conn, run))
        self.assertEqual(len(evaluate.run_results(self.conn, run)), 0)
        self.assertEqual(len(evaluate.list_runs(self.conn, 1)), 0)

    def test_compare_runs(self):
        a = evaluate.create_run(self.conn, 1, "m", "anthropic")
        b = evaluate.create_run(self.conn, 1, "m", "deepseek")
        # p0: both keep (agree). p1: a keep, b drop (disagree). p2: only in a (not shared).
        evaluate.save_page(self.conn, a, 1, "https://www.gov.uk/p0", {"keep": 1, "score": .9, "reason": ""}, 1)
        evaluate.save_page(self.conn, a, 1, "https://www.gov.uk/p1", {"keep": 1, "score": .8, "reason": ""}, 1)
        evaluate.save_page(self.conn, a, 1, "https://www.gov.uk/p2", {"keep": 1, "score": .7, "reason": ""}, 1)
        evaluate.save_page(self.conn, b, 1, "https://www.gov.uk/p0", {"keep": 1, "score": .9, "reason": ""}, 1)
        evaluate.save_page(self.conn, b, 1, "https://www.gov.uk/p1", {"keep": 0, "score": .3, "reason": ""}, 1)
        c = evaluate.compare(self.conn, a, b)
        self.assertEqual(c["shared"], 2)      # p0, p1 (p2 only in a)
        self.assertEqual(c["kept"], 1)        # b kept p0
        self.assertEqual(c["dropped"], 1)     # b dropped p1
        self.assertEqual(c["disagree"], 1)    # p1 differs

    def test_list_runs_and_results_filter(self):
        run = evaluate.create_run(self.conn, 1, "m", "anthropic")
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p0", {"keep": 1, "score": 0.9, "reason": "a"}, 10)
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p1", {"keep": 0, "score": 0.2, "reason": "b"}, 10)
        self.assertEqual(len(evaluate.list_runs(self.conn, 1)), 1)
        keep = evaluate.run_results(self.conn, run, keep=1)
        self.assertEqual([r["url"] for r in keep], ["https://www.gov.uk/p0"])


class TestExclusion(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_exclusion_prompt_has_criteria_and_defaults_keep(self):
        p = evaluate.build_exclusion_prompt(
            "Slurry", "slurry storage", "sewage sludge is out of scope",
            "keep planning permission pages", "drop homonym 'slurry pump' catalogue",
            "Title", "x" * 9000, "pass 1 said relevant", body_limit=100)
        self.assertIn("sewage sludge is out of scope", p)
        self.assertIn("pass 1 said relevant", p)
        self.assertIn("Default to KEEP", p)
        self.assertIn("x" * 100, p)
        self.assertNotIn("x" * 101, p)

    def test_parse_exclusion_tags_hit(self):
        d = evaluate.parse_exclusion('{"keep": false, "exclusion_hit": "homonym", "reason": "wrong slurry"}')
        self.assertEqual(d["keep"], 0)
        self.assertIn("[homonym]", d["reason"])
        keep = evaluate.parse_exclusion('{"keep": true, "exclusion_hit": "none", "reason": "on topic"}')
        self.assertEqual(keep["keep"], 1)
        self.assertNotIn("[", keep["reason"])
        self.assertIsNone(evaluate.parse_exclusion("garbage"))

    def test_exclusion_candidates_are_source_keeps_only(self):
        for i in range(3):
            self.conn.execute("INSERT INTO content (url, title, description, search_text) VALUES (?,?,?,?)",
                              (f"https://www.gov.uk/p{i}", f"T{i}", f"D{i}", f"body {i}"))
        inc = evaluate.create_run(self.conn, 1, "m", "anthropic")
        evaluate.save_page(self.conn, inc, 1, "https://www.gov.uk/p0", {"keep": 1, "score": .9, "reason": "keep0"}, 1)
        evaluate.save_page(self.conn, inc, 1, "https://www.gov.uk/p1", {"keep": 0, "score": .1, "reason": "drop1"}, 1)
        evaluate.save_page(self.conn, inc, 1, "https://www.gov.uk/p2", {"keep": 1, "score": .8, "reason": "keep2"}, 1)
        exc = evaluate.create_run(self.conn, 1, "m2", "anthropic",
                                  phase=evaluate.PHASE_EXCLUSION, source_run_id=inc)
        cands = evaluate.exclusion_candidates(self.conn, exc, inc, 10)
        urls = sorted(c["url"] for c in cands)
        self.assertEqual(urls, ["https://www.gov.uk/p0", "https://www.gov.uk/p2"])  # only the keeps
        self.assertEqual(cands[0]["pass1_reason"], "keep0")
        # After evaluating p0 in the exclusion run, it is no longer a candidate.
        evaluate.save_page(self.conn, exc, 1, "https://www.gov.uk/p0", {"keep": 1, "score": None, "reason": "ok"}, 1)
        left = evaluate.exclusion_candidates(self.conn, exc, inc, 10)
        self.assertEqual([c["url"] for c in left], ["https://www.gov.uk/p2"])
        self.assertEqual(evaluate.kept_count(self.conn, inc), 2)


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
            "actual_model": "claude-haiku-4-5-20251001-actual",
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
        self.assertEqual(run["phase"], evaluate.PHASE_INCLUSION)             # phase tracked
        self.assertEqual(run["provider"], "anthropic")                       # supplier tracked
        self.assertEqual(run["actual_model"], "claude-haiku-4-5-20251001-actual")  # served model tracked
        self.assertGreater(app._daily_spend(conn), 0)
        conn.close()

    def _bg_status(self, app):
        return {"running": True, "done": 0, "cost": 0.0, "phase": None, "remaining": None,
                "run_id": None, "stopped": None, "error": None,
                "started_at": app.db.now_iso(), "finished_at": None}

    def test_background_loop_runs_to_completion(self):
        import threading
        app, cid = self._app(n=5)
        status = self._bg_status(app)
        app._background_eval_loop(cid, threading.Event(), status)   # runs synchronously here
        self.assertFalse(status["running"])
        self.assertIsNone(status["error"])
        self.assertGreaterEqual(status["done"], 5)
        conn = app.connect()
        phases = {r["phase"] for r in evaluate.list_runs(conn, cid)}
        conn.close()
        # Ran the inclusion phase and auto-advanced through exclusion — no browser needed.
        self.assertIn(evaluate.PHASE_INCLUSION, phases)
        self.assertIn(evaluate.PHASE_EXCLUSION, phases)

    def test_background_loop_respects_stop(self):
        import threading
        app, cid = self._app(n=5)
        ev = threading.Event(); ev.set()               # already stopped
        status = self._bg_status(app)
        app._background_eval_loop(cid, ev, status)
        self.assertFalse(status["running"])
        self.assertEqual(status["done"], 0)            # nothing evaluated

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
        # Two inclusion runs (each may auto-spawn a Phase-2 exclusion run over its keeps).
        inclusion = [r for r in evaluate.list_runs(conn, cid)
                     if r["phase"] == evaluate.PHASE_INCLUSION]
        self.assertEqual(len(inclusion), 2)
        conn.close()

    def test_budget_stops_and_max_docs_caps(self):
        app, cid = self._app(n=5, cost=0.0002)
        self._set(app, "ai_daily_budget", "0.0003")
        result = app._run_evaluation(cid, 5)
        self.assertEqual(result["stopped"], "budget")
        self.assertLessEqual(result["evaluated_this_run"], 2)


class TestRunCommentary(unittest.TestCase):
    def _run(self, **kw):
        base = {"run_id": "x", "phase": evaluate.PHASE_INCLUSION, "pages": 0, "kept": 0,
                "dropped": 0, "unparseable": 0, "finished_at": None, "source_run_id": None}
        base.update(kw)
        return base

    def test_perfect_reconcile_and_no_errors(self):
        inc = self._run(run_id="i", pages=100, kept=60, dropped=40, finished_at="t")
        c = evaluate.run_commentary([inc], shortlist_total=100)
        self.assertEqual(c["reconcile"][0]["kind"], "ok")
        self.assertEqual(c["errors"], [])
        self.assertTrue(any("complete" in n["text"] for n in c["phases"]))
        # inclusion done with keeps and no exclusion -> pending-exclusion note
        self.assertTrue(any("not run yet" in n["text"] for n in c["phases"]))

    def test_unparseable_explained_in_reconcile_and_errors(self):
        inc = self._run(run_id="i", pages=571, kept=155, dropped=415, unparseable=1, finished_at="t")
        c = evaluate.run_commentary([inc], shortlist_total=571)
        self.assertTrue(any("could not be" in n["text"] for n in c["errors"]))
        rec = c["reconcile"][0]
        self.assertEqual(rec["kind"], "warn")           # 155 + 415 != 571 because of the 1 unparseable
        self.assertIn("unparseable", rec["text"])

    def test_exclusion_input_is_inclusion_keeps(self):
        inc = self._run(run_id="i", phase=evaluate.PHASE_INCLUSION, pages=100, kept=60, dropped=40, finished_at="t")
        exc = self._run(run_id="e", phase=evaluate.PHASE_EXCLUSION, source_run_id="i",
                        pages=50, kept=45, dropped=5, finished_at=None)
        c = evaluate.run_commentary([inc, exc], shortlist_total=100)
        exc_rec = [n for n in c["reconcile"] if "Exclusion" in n["text"]][0]
        self.assertEqual(exc_rec["kind"], "warn")       # input 60 (kept) vs 45+5 evaluated so far
        self.assertIn("not evaluated yet", exc_rec["text"])
        self.assertTrue(any("has not finished" in n["text"] for n in c["errors"]))


if __name__ == "__main__":
    unittest.main()
