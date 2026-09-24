"""AI evaluation tests: pure prompt/parse, run tracking, and a mocked run.
Run: python3 -m unittest -v tests.test_evaluate"""
from __future__ import annotations

import json
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
        self.assertNotIn("sewage sludge", p)      # phase 1 no longer carries the EXCLUDE text
        self.assertNotIn("EXCLUDE", p)
        self.assertIn("Title", p)
        self.assertIn("x" * 100, p)
        self.assertNotIn("x" * 101, p)

    def test_parse_plain_json(self):
        d = evaluate.parse_decision('{"keep": true, "score": 0.9, "reason": "on topic"}')
        # The three decision fields are unchanged; the grounding fields (primary_topic / where_hit /
        # evidence) are always present in the return shape and None for a reply that omits them.
        self.assertEqual({k: d[k] for k in ("keep", "score", "reason")},
                         {"keep": 1, "score": 0.9, "reason": "on topic"})
        self.assertEqual({d["primary_topic"], d["where_hit"], d["evidence"]}, {None})

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

    def test_unparsed_results(self):
        run = evaluate.create_run(self.conn, 1, "m", "anthropic")
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p0", {"keep": 1, "score": .9, "reason": "ok"}, 10)
        evaluate.save_page(self.conn, run, 1, "https://www.gov.uk/p1", None, 10)   # unparseable -> keep NULL
        up = evaluate.unparsed_results(self.conn, [run])
        self.assertEqual([u["url"] for u in up], ["https://www.gov.uk/p1"])
        self.assertEqual(up[0]["reason"], "unparseable model reply")

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
        self.assertIn("Inclusion criteria:\nslurry storage", p)   # both halves labelled
        self.assertIn("Exclusion criteria:\nsewage sludge is out of scope", p)
        self.assertIn("pass 1 said relevant", p)
        # No exclusion text -> the exclusion half still shows, with (none given).
        p2 = evaluate.build_exclusion_prompt("Probate", "Probate", "", "", "", "T", "b", "note")
        self.assertIn("Exclusion criteria:\n(none given)", p2)
        self.assertIn("Default to KEEP", p)
        self.assertIn("x" * 100, p)
        self.assertNotIn("x" * 101, p)

    def test_confidence_label_bands(self):
        self.assertEqual(evaluate.confidence_label(0), "Wrong sense")
        self.assertEqual(evaluate.confidence_label(0.2), "Mentioned in passing")
        self.assertEqual(evaluate.confidence_label(0.5), "Discussed a moderate amount")
        self.assertEqual(evaluate.confidence_label(0.9), "Major focus")
        self.assertEqual(evaluate.confidence_label(None), "")

    def test_parse_exclusion_tags_hit(self):
        d = evaluate.parse_exclusion('{"keep": false, "exclusion_hit": "homonym", "reason": "wrong slurry"}')
        self.assertEqual(d["keep"], 0)
        self.assertIn("[homonym]", d["reason"])
        keep = evaluate.parse_exclusion('{"keep": true, "exclusion_hit": "none", "reason": "on topic"}')
        self.assertEqual(keep["keep"], 1)
        self.assertNotIn("[", keep["reason"])
        self.assertIsNone(evaluate.parse_exclusion("garbage"))

    def test_parse_decision_captures_grounding_fields(self):
        import json as _json
        d = evaluate.parse_decision(
            '{"keep": true, "score": 0.8, "where": ["title", "body"], '
            '"evidence": ["nitrate vulnerable zones"], "primary_topic": "Nitrate rules for farmers", '
            '"reason": "on topic"}')
        self.assertEqual(d["keep"], 1)
        self.assertEqual(d["primary_topic"], "Nitrate rules for farmers")
        self.assertEqual(_json.loads(d["where_hit"]), ["title", "body"])
        self.assertEqual(_json.loads(d["evidence"]), ["nitrate vulnerable zones"])
        # Legacy / exclusion-style replies without the grounding fields still parse; fields are None.
        legacy = evaluate.parse_decision('{"keep": false, "score": 0.0, "reason": "wrong sense"}')
        self.assertEqual(legacy["keep"], 0)
        self.assertIsNone(legacy["primary_topic"])
        self.assertIsNone(legacy["where_hit"])
        self.assertIsNone(legacy["evidence"])

    def test_exclusion_prompt_carries_pass1_topic(self):
        p = evaluate.build_exclusion_prompt("Slurry", "slurry storage", "", "", "", "T", "b", "note",
                                            pass1_topic="Farm waste storage rules")
        self.assertIn("mainly about: Farm waste storage rules", p)
        p2 = evaluate.build_exclusion_prompt("Slurry", "slurry storage", "", "", "", "T", "b", "note")
        self.assertIn("mainly about: (not given)", p2)

    def test_legacy_composite_template_reproduces_old_assembly(self):
        # A template stamped before the atomic fields still renders exactly as the old Python
        # assembly did: composed SPEC, conditional KEEP/DROP sections, TITLE_LINE, repr()'d note.
        tmpl = "{{SPEC}}|{{KEEP_SECTION}}|{{DROP_SECTION}}|{{TITLE_LINE}}|{{PASS1_REASON}}|{{BODY}}"
        p = evaluate.build_exclusion_prompt("Slurry", " slurry storage ", "", "keep farms", "",
                                            "T", "b", "pass 1 said relevant", template=tmpl)
        self.assertEqual(
            p,
            "Inclusion criteria:\nslurry storage\n\nExclusion criteria:\n(none given)"
            "|\nKEEP examples (keep = true) — lean toward keeping when similar content appears:\nkeep farms\n"
            "||Page title: T\n|'pass 1 said relevant'|b")
        # No title and no hints -> those blocks are empty strings, exactly as before.
        p2 = evaluate.build_exclusion_prompt("S", "i", "", "", "", "", "b", "n", template=tmpl)
        self.assertIn("|||", p2)
        self.assertNotIn("{{", p2)

    def test_atomic_default_always_shows_sections(self):
        p = evaluate.build_exclusion_prompt("Slurry", "slurry storage", "", "", "", "T", "b", "note")
        self.assertIn("KEEP examples (keep = true)", p)
        self.assertIn("DROP examples (keep = false)", p)
        self.assertEqual(p.count("\n(none)\n"), 2)     # empty keep + drop hints render as (none)
        self.assertIn("Exclusion criteria:\n(none given)", p)
        self.assertIn("Page title: T\n", p)
        self.assertNotIn("{{", p)

    def test_render_phase_prompt_is_plain_fill(self):
        out = evaluate.render_phase_prompt(
            evaluate.EXCLUSION_FIELDS, "A {{INCLUDE}} B {{KEEP}} C {{NAME_UPPER}}",
            {"inclusion": "x", "name_upper": "SLURRY"}, {})
        self.assertEqual(out, "A x B (none) C SLURRY")

    def test_template_fingerprint_identifies_prompt_text(self):
        a = evaluate.template_fingerprint("x {{BODY}}")
        self.assertEqual(a, evaluate.template_fingerprint("x {{BODY}}"))    # deterministic
        self.assertEqual(len(a), 8)
        self.assertNotEqual(a, evaluate.template_fingerprint("y {{BODY}}"))  # any text change -> new hash
        self.assertEqual(evaluate.template_fingerprint(""), "")
        self.assertEqual(evaluate.template_fingerprint(None), "")

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

    def test_transient_error_skips_page_and_continues(self):
        # One transient AI error should skip that page (record it unscored) and carry on,
        # not abort the whole chunk.
        app, cid = self._app(n=4)
        calls = {"n": 0}
        ok = {"reply": '{"keep": true, "score": 0.8, "reason": "r"}', "actual_model": "m",
              "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0}
        def flaky(cfg, system, prompt):
            calls["n"] += 1
            return {"error": "TimeoutError: slow"} if calls["n"] == 2 else dict(ok)
        app._ai_reply = flaky
        result = app._run_evaluation(cid, 4)
        self.assertNotIn("error", result)                 # not aborted
        self.assertEqual(result["evaluated_this_run"], 4) # all 4 progressed
        self.assertEqual(result["skipped"], 1)            # one recorded as skipped
        conn = app.connect()
        run = evaluate.get_run(conn, result["run_id"])
        self.assertEqual(run["pages"], 4)
        self.assertEqual(run["kept"], 3)                  # 3 kept, 1 unscored (skipped)
        self.assertEqual(run["unparseable"], 1)
        conn.close()

    def test_consecutive_errors_stop_the_run(self):
        # A provider that errors on every page should stop after MAX_CONSEC_EVAL_ERRORS,
        # not mark every page unscored.
        app, cid = self._app(n=12)   # more pages than the consecutive-error threshold
        app._ai_reply = lambda cfg, system, prompt: {"error": "APIError: 503"}
        result = app._run_evaluation(cid, 50)
        self.assertEqual(result["stopped"], "errors")
        self.assertIn("error", result)
        self.assertEqual(result["evaluated_this_run"], app.MAX_CONSEC_EVAL_ERRORS - 1)  # only skips before the bail

    def test_fatal_error_aborts_immediately(self):
        app, cid = self._app(n=4)
        app._ai_reply = lambda cfg, system, prompt: {"fatal": True, "error": "No API key set for X."}
        result = app._run_evaluation(cid, 4)
        self.assertTrue(result.get("fatal"))
        self.assertEqual(result["evaluated_this_run"], 0)   # nothing recorded


    def _scoped_run(self, app, cid, urls, bench=None, temperature=0.0):
        conn = app.connect()
        from govuk_corpus import settings as st
        spec = evaluate.prompt_spec_json(conn, cid, evaluate.PHASE_INCLUSION,
                                         {"sampling": {"temperature": temperature}},
                                         scope={"kind": "gold", "urls": urls, "sha": "abc"}, bench=bench)
        run_id = evaluate.create_run(conn, cid, "m", "anthropic", prompt_spec=spec, name="bench/x/r1")
        st.set_setting(conn, f"active_run_{cid}", run_id)
        conn.close()
        return run_id

    def test_scoped_run_evaluates_exactly_the_scope_and_inherits_model_for_phase2(self):
        app, cid = self._app(n=5)
        seen = []
        app._ai_reply = lambda cfg, system, prompt, **kw: (seen.append(kw) or {
            "reply": '{"keep": true, "score": 0.8, "reason": "relevant"}',
            "actual_model": "m-actual", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.0002})
        scope = ["https://www.gov.uk/p0", "https://www.gov.uk/p3"]
        run_id = self._scoped_run(app, cid, scope)
        result = app._run_evaluation(cid, 10)
        self.assertEqual(result["run_id"], run_id)
        self.assertEqual(result["evaluated_this_run"], 2)          # not the 5-page shortlist
        self.assertEqual(result["remaining"], 0)
        # The pinned sampling reached the model hook.
        self.assertTrue(all(kw.get("sampling", {}).get("temperature") == 0.0 for kw in seen))
        conn = app.connect()
        urls = sorted(r["url"] for r in evaluate.run_results(conn, run_id))
        self.assertEqual(urls, scope)
        row = conn.execute("SELECT content_hash FROM evaluation_results WHERE run_id=? AND url=?",
                           (run_id, scope[0])).fetchone()
        self.assertEqual(row["content_hash"], "h")                # the evaluated body is stamped
        self.assertTrue(evaluate.get_run(conn, run_id)["finished_at"])
        # Auto-advanced Phase 2 inherits the scoped run's model (not the app's phase model)…
        adv = result["advanced"]
        self.assertIsNotNone(adv)
        excl = evaluate.get_run(conn, adv["run_id"])
        self.assertEqual((excl["model"], excl["provider"]), ("m", "anthropic"))
        # …and carries the scope + sampling forward.
        spec = evaluate.run_prompt_spec(conn, adv["run_id"])
        self.assertEqual(spec["scope"]["urls"], scope)
        self.assertEqual(spec["temperature"], 0.0)
        conn.close()

    def test_bench_run_is_never_auto_advanced(self):
        app, cid = self._app(n=3)
        app._ai_reply = lambda cfg, system, prompt, **kw: {
            "reply": '{"keep": true, "score": 0.8, "reason": "relevant"}',
            "actual_model": "m-actual", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.0002}
        run_id = self._scoped_run(app, cid, ["https://www.gov.uk/p1"], bench={"arm": "p1-haiku", "repeat": 1})
        result = app._run_evaluation(cid, 10)
        self.assertEqual(result["evaluated_this_run"], 1)
        self.assertIsNone(result["advanced"])
        conn = app.connect()
        self.assertTrue(evaluate.get_run(conn, run_id)["finished_at"])
        self.assertEqual(len(evaluate.list_runs(conn, cid)), 1)
        self.assertEqual(evaluate.list_runs(conn, cid)[0]["trial"]["scope_n"], 1)
        self.assertTrue(evaluate.list_runs(conn, cid)[0]["trial"]["bench"])
        conn.close()


class TestScopedAndReconcile(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        rows = [  # slug, doc type, redirect, hash, withdrawn, content_id
            ("p0", "guidance", 0, "h0", 0, "c0"), ("p1", "guidance", 0, "h1", 0, "c1"),
            ("other", "speech", 0, "h2", 0, "c2"),          # outside the category's filters
            ("redir", "guidance", 1, "h3", 0, "c3"), ("unfetched", "guidance", 0, None, 0, "c4"),
            ("gone", "guidance", 0, "h5", 1, "c5"), ("govuk-only", "guidance", 0, "h6", 0, "c6"),
        ]
        for slug, dt, redir, h, wd, cidv in rows:
            self.conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, withdrawn, "
                              "search_text, title, content_id) VALUES (?,?,?,?,?,?,?,?)",
                              (f"https://www.gov.uk/{slug}", dt, redir, h, wd, "slurry", slug, cidv))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_scoped_candidates_only_scope_urls_minus_unusable_and_evaluated(self):
        u = lambda s: f"https://www.gov.uk/{s}"
        run = evaluate.create_run(self.conn, 1, "m", "anthropic")
        scope = [u("p1"), u("other"), u("redir"), u("unfetched"), u("gone"), u("p0"), "https://www.gov.uk/missing"]
        got = evaluate.scoped_candidates(self.conn, run, scope, 10)
        self.assertEqual([r["url"] for r in got], [u("other"), u("p0"), u("p1")])   # sorted, filters ignored
        self.assertEqual(got[1]["content_hash"], "h0")
        evaluate.save_page(self.conn, run, 1, u("p0"), {"keep": 1, "score": 0.9, "reason": "x"}, 5,
                           content_hash="h0")
        self.assertEqual([r["url"] for r in evaluate.scoped_candidates(self.conn, run, scope, 10)],
                         [u("other"), u("p1")])
        self.assertEqual([r["url"] for r in evaluate.scoped_candidates(self.conn, run, scope, 1)], [u("other")])
        self.assertEqual(evaluate.scoped_candidates(self.conn, run, [], 10), [])
        row = self.conn.execute("SELECT content_hash FROM evaluation_results WHERE run_id=?", (run,)).fetchone()
        self.assertEqual(row["content_hash"], "h0")

    def test_reconcile_keeps_govuk_only_and_scoped_results(self):
        u = lambda s: f"https://www.gov.uk/{s}"
        cid = 7
        # Shortlist membership: p0 only. govuk-only is forwarded via category_search_pages (source='search').
        self.conn.execute("INSERT INTO category_shortlist_pages (category_id, content_id, url) VALUES (?,?,?)",
                          (cid, "c0", u("p0")))
        self.conn.execute("INSERT INTO category_search_pages (category_id, url, source, es_score) VALUES (?,?,?,?)",
                          (cid, u("govuk-only"), "search", 0.5))
        normal = evaluate.create_run(self.conn, cid, "m", "anthropic")
        for slug in ("p0", "p1", "govuk-only"):
            evaluate.save_page(self.conn, normal, cid, u(slug), {"keep": 1, "score": 0.9, "reason": "x"}, 5)
        scoped = evaluate.create_run(self.conn, cid, "m", "anthropic", prompt_spec=evaluate.prompt_spec_json(
            self.conn, cid, evaluate.PHASE_INCLUSION, None, scope={"kind": "gold", "urls": [u("p1")], "sha": "s"}))
        evaluate.save_page(self.conn, scoped, cid, u("p1"), {"keep": 0, "score": 0.0, "reason": "y"}, 5)
        evaluate.reconcile_to_shortlist(self.conn, cid)
        left = sorted((r["run_id"], r["url"]) for r in self.conn.execute(
            "SELECT run_id, url FROM evaluation_results").fetchall())
        self.assertEqual(left, sorted([(normal, u("p0")), (normal, u("govuk-only")), (scoped, u("p1"))]))
        self.assertEqual(evaluate.get_run(self.conn, normal)["pages"], 2)     # p1 dropped (not forwarded)
        self.assertEqual(evaluate.get_run(self.conn, scoped)["pages"], 1)     # scoped run untouched

    def test_prompt_spec_stamps_request_controls_and_scope(self):
        spec = json.loads(evaluate.prompt_spec_json(self.conn, 1, evaluate.PHASE_INCLUSION,
                                                    {"sampling": {"temperature": 0, "thinking": None}},
                                                    scope={"kind": "gold", "urls": ["a"], "sha": "z"},
                                                    bench={"arm": "p1-haiku"}))
        self.assertEqual(spec["temperature"], 0.0)
        self.assertIsNone(spec["thinking"])
        self.assertEqual(spec["max_tokens"], 4096)
        self.assertEqual(spec["template_hash"], evaluate.template_fingerprint(spec["template"]))
        self.assertEqual(spec["scope"]["sha"], "z")
        self.assertEqual(spec["bench"]["arm"], "p1-haiku")
        plain = json.loads(evaluate.prompt_spec_json(self.conn, 1, evaluate.PHASE_INCLUSION))
        self.assertIsNone(plain["temperature"])          # product runs: provider defaults, truthfully
        self.assertNotIn("scope", plain)
        self.assertEqual(evaluate.norm_trial(plain)["sampling"]["temperature"], None)


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

    def test_next_steps_complete_unfinished_opened_run(self):
        inc = self._run(run_id="i", pages=300, kept=0, dropped=0, finished_at=None)  # in progress
        c = evaluate.run_commentary([inc], shortlist_total=571, opened_run_id="i")
        self.assertTrue(any(n["text"].startswith("Complete this run") for n in c["next_steps"]))
        self.assertTrue(any("271 of 571" in n["text"] for n in c["next_steps"]))     # remaining

    def test_next_steps_run_exclusion_when_pending(self):
        inc = self._run(run_id="i", pages=571, kept=155, dropped=415, finished_at="t")  # done, has keeps
        c = evaluate.run_commentary([inc], shortlist_total=571, opened_run_id="i")
        self.assertTrue(any("Phase 2 (Exclusion)" in n["text"] for n in c["next_steps"]))

    def test_next_steps_flags_unparseable(self):
        inc = self._run(run_id="i", pages=571, kept=155, dropped=415, unparseable=1, finished_at="t")
        c = evaluate.run_commentary([inc], shortlist_total=571, opened_run_id="i")
        self.assertTrue(any("could not be parsed" in n["text"] for n in c["next_steps"]))

    def test_continuable_in_progress(self):
        inc = self._run(run_id="i", pages=100, kept=60, dropped=40, finished_at=None)
        self.assertIn("in progress", evaluate.continuable_reason([inc], shortlist_total=200))

    def test_continuable_finished_but_short(self):
        inc = self._run(run_id="i", pages=100, kept=60, dropped=40, finished_at="t")
        self.assertIn("100 of 200", evaluate.continuable_reason([inc], shortlist_total=200))

    def test_continuable_pending_exclusion(self):
        inc = self._run(run_id="i", pages=200, kept=60, dropped=140, finished_at="t")
        self.assertIn("Exclusion", evaluate.continuable_reason([inc], shortlist_total=200))

    def test_continuable_none_when_complete(self):
        inc = self._run(run_id="i", pages=200, kept=60, dropped=140, finished_at="t")
        exc = self._run(run_id="e", phase=evaluate.PHASE_EXCLUSION, source_run_id="i",
                        pages=60, kept=55, dropped=5, finished_at="t")
        self.assertIsNone(evaluate.continuable_reason([inc, exc], shortlist_total=200))


if __name__ == "__main__":
    unittest.main()
