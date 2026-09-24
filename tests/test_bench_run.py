"""govuk_corpus.bench end-to-end on SQLite with the dry-run model: run names + stamps, resume
after a crash without duplicates, budget stop, estimate and report.
Run: python3 -m unittest -v tests.test_bench_run"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import bench, db, evaluate, gold, settings

CID = 1789943869717


class BenchBase(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        c = self.conn
        pages = [("p0", "slurry storage is regulated"), ("p1", "slurry pits need cover"),
                 ("p2", "nothing to see here"), ("p3", "rules for slurry"), ("p4", "unrelated speech")]
        for slug, body in pages:
            url = f"https://www.gov.uk/{slug}"
            c.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, search_text, "
                      "title, description, content_id) VALUES (?,?,0,?,?,?,?,?)",
                      (url, "guidance", f"hash-{slug}", body, f"Title {slug}", f"Desc {slug}", f"cid-{slug}"))
            c.execute("INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                      "VALUES (?,?,?,?)", (url, "environment-agency", "environment-agency", "primary"))
        c.execute("INSERT INTO categories (id, slug, description, dept_slugs, document_type_slugs, keywords, "
                  "inclusion_context, exclusion_context) VALUES (?,?,?,?,?,?,?,?)",
                  (CID, "slurry", "Slurry", "environment-agency", "guidance", "slurry",
                   "slurry storage rules", "sewage sludge is out"))
        labels = {"p0": "in", "p1": "in", "p2": "out", "p3": "borderline", "p4": "out"}
        for slug, lbl in labels.items():
            gold.upsert_label(c, CID, {"url": f"https://www.gov.uk/{slug}", "label": lbl, "rationale": "t",
                                       "content_hash_at_label": f"hash-{slug}", "labelled_by": "t@x"})
        c.commit()
        self.log = []

    def tearDown(self):
        self.conn.close()

    def _runs(self):
        return {r["name"]: r for r in evaluate.list_runs(self.conn, CID)}


class TestRun(BenchBase):
    def test_dry_run_creates_named_stamped_runs_for_both_phases(self):
        res = bench.run(self.conn, CID, "haiku", repeats=2, concurrency=2, dry_run=True, log=self.log.append)
        self.assertEqual(res["status"], "complete")
        runs = self._runs()
        self.assertEqual(sorted(runs), ["bench/p1-haiku-p2-haiku/r1", "bench/p1-haiku-p2-haiku/r2",
                                        "bench/p1-haiku-p2-sonnet46/r1", "bench/p1-haiku-p2-sonnet46/r2",
                                        "bench/p1-haiku/r1", "bench/p1-haiku/r2"])
        p1 = runs["bench/p1-haiku/r1"]
        self.assertEqual((p1["pages"], p1["kept"], p1["dropped"]), (5, 3, 2))   # p0 p1 p3 mention slurry
        self.assertTrue(p1["finished_at"])
        self.assertEqual(p1["model"], "claude-haiku-4-5-20251001")
        spec = evaluate.run_prompt_spec(self.conn, p1["run_id"])
        self.assertEqual(spec["temperature"], 0.0)
        self.assertIsNone(spec["thinking"])
        self.assertEqual(spec["scope"]["n"], 5)
        self.assertEqual(spec["bench"], {"arm": "p1-haiku", "p1": "haiku", "repeat": 1, "dry_run": True})
        self.assertEqual(p1["trial"]["scope_n"], 5)
        p2 = runs["bench/p1-haiku-p2-sonnet46/r1"]
        self.assertEqual(p2["source_run_id"], p1["run_id"])
        self.assertEqual(p2["model"], "claude-sonnet-4-6")
        self.assertEqual(p2["pages"], 3)                                          # only the P1 keeps
        self.assertEqual(evaluate.run_prompt_spec(self.conn, p2["run_id"])["bench"]["p2"], "sonnet46")
        # both P2 models were driven off the SAME P1 run
        self.assertEqual(runs["bench/p1-haiku-p2-haiku/r1"]["source_run_id"], p1["run_id"])
        # the evaluated body hash + stop_reason are stamped per row
        row = self.conn.execute("SELECT content_hash, stop_reason FROM evaluation_results WHERE run_id=? AND url=?",
                                (p1["run_id"], "https://www.gov.uk/p0")).fetchone()
        self.assertEqual((row["content_hash"], row["stop_reason"]), ("hash-p0", "end_turn"))
        # usage rows are tagged
        kinds = {r["kind"] for r in self.conn.execute("SELECT kind FROM ai_usage").fetchall()}
        self.assertEqual(kinds, {"bench"})
        # the app's active run is untouched
        self.assertEqual(settings.get_setting(self.conn, f"active_run_{CID}", ""), "")
        # ai_models got the Sonnet 4.6 row
        from govuk_corpus import ai_models
        self.assertIsNotNone(ai_models.find(self.conn, "anthropic", "claude-sonnet-4-6"))

    def test_crash_then_rerun_resumes_without_duplicates(self):
        calls = {"n": 0}

        def flaky(cfg, system, prompt, **kw):
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("boom")
            return bench.fake_reply(cfg, system, prompt, **kw)
        with self.assertRaises(RuntimeError):
            bench.run(self.conn, CID, "haiku", repeats=1, concurrency=1, dry_run=True, reply_fn=flaky,
                      log=self.log.append)
        runs = self._runs()
        self.assertEqual(list(runs), ["bench/p1-haiku/r1"])
        self.assertEqual(runs["bench/p1-haiku/r1"]["pages"], 2)
        self.assertIsNone(runs["bench/p1-haiku/r1"]["finished_at"])
        res = bench.run(self.conn, CID, "haiku", repeats=1, concurrency=1, dry_run=True, log=self.log.append)
        self.assertEqual(res["status"], "complete")
        runs = self._runs()
        self.assertEqual(sorted(runs), ["bench/p1-haiku-p2-haiku/r1", "bench/p1-haiku-p2-sonnet46/r1", "bench/p1-haiku/r1"])
        self.assertEqual(runs["bench/p1-haiku/r1"]["pages"], 5)
        dup = self.conn.execute("SELECT run_id, url, COUNT(*) AS n FROM evaluation_results GROUP BY run_id, url "
                                "HAVING n > 1").fetchall()
        self.assertEqual(dup, [])
        self.assertTrue(any("resuming" in m for m in self.log))

    def test_budget_stop_marks_run_stopped_and_rerun_continues(self):
        settings.set_setting(self.conn, "ai_daily_budget", "0.5")
        self.conn.execute("INSERT INTO ai_usage (day, created_at, cost, input_tokens, output_tokens, kind) "
                          "VALUES (?,?,?,?,?,?)", (db.now_iso()[:10], db.now_iso(), 1.0, 1, 1, "evaluate"))
        self.conn.commit()
        res = bench.run(self.conn, CID, "haiku", repeats=1, dry_run=True, log=self.log.append)
        self.assertEqual(res["status"], "budget")
        r = self._runs()["bench/p1-haiku/r1"]
        self.assertEqual((r["pages"], r["run_status"]), (0, "stopped"))
        settings.set_setting(self.conn, "ai_daily_budget", "0")          # 0 = unlimited
        res = bench.run(self.conn, CID, "haiku", repeats=1, dry_run=True, log=self.log.append)
        self.assertEqual(res["status"], "complete")
        self.assertEqual(self._runs()["bench/p1-haiku/r1"]["pages"], 5)

    def test_drift_refused_unless_allowed(self):
        self.conn.execute("UPDATE content SET content_hash='changed' WHERE url='https://www.gov.uk/p2'")
        self.conn.commit()
        with self.assertRaises(SystemExit):
            bench.run(self.conn, CID, "haiku", repeats=1, dry_run=True, log=self.log.append)
        self.assertEqual(self._runs(), {})
        res = bench.run(self.conn, CID, "haiku", repeats=1, dry_run=True, allow_drift=True, log=self.log.append)
        self.assertEqual(res["status"], "complete")

    def test_gold_change_means_new_runs_not_resume(self):
        bench.run(self.conn, CID, "haiku", repeats=1, dry_run=True, log=self.log.append)
        gold.upsert_label(self.conn, CID, {"url": "https://www.gov.uk/p3", "label": "in", "rationale": "changed"})
        self.conn.commit()
        bench.run(self.conn, CID, "haiku", repeats=1, dry_run=True, log=self.log.append)
        n = self.conn.execute("SELECT COUNT(*) AS n FROM evaluation_runs WHERE name='bench/p1-haiku/r1'").fetchone()["n"]
        self.assertEqual(n, 2)   # a second r1 against the new gold fingerprint


class TestEstimateAndReport(BenchBase):
    def test_estimate_shape(self):
        e = bench.estimate(self.conn, CID, "all", repeats=5)
        self.assertEqual(e["pages"], 5)
        self.assertEqual(set(e["arms"]), {"haiku", "sonnet46"})
        self.assertGreater(e["arms"]["sonnet46"]["p1_per_run"], e["arms"]["haiku"]["p1_per_run"])
        self.assertEqual(set(e["arms"]["haiku"]["p2_per_run"]), {"haiku", "sonnet46"})
        self.assertGreater(e["total"], 0)
        self.assertIn("fits_budget", e)

    def test_report_bundle(self):
        bench.run(self.conn, CID, "all", repeats=2, concurrency=2, dry_run=True, log=self.log.append)
        with tempfile.TemporaryDirectory() as d:
            s = bench.report(self.conn, CID, d, log=self.log.append)
            files = sorted(os.listdir(d))
            self.assertEqual(files, ["grounding.csv", "paired.csv", "per_page.csv", "per_run.csv",
                                     "results.md", "runs.json", "summary.json"])   # no flips -> no stability.csv
            with open(os.path.join(d, "results.md"), encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn("Phase-1 recall", md)
            self.assertIn("p1-haiku", md)
            self.assertIn("haiku → sonnet46", md)
            with open(os.path.join(d, "runs.json"), encoding="utf-8") as fh:
                runs = json.load(fh)
            self.assertEqual(len(runs), 12)
            self.assertTrue(all(r["prompt_spec"]["scope"]["sha"] == s["gold_sha"] for r in runs))
        self.assertEqual(s["n"], 5)
        self.assertEqual(s["labels"], {"in": 2, "out": 2, "borderline": 1})
        h = s["arms"]["haiku"]
        d1 = h["phase1"]["D"]["micro"]
        self.assertEqual((d1["tp"], d1["fp"], d1["fn"], d1["tn"]), (4, 0, 0, 4))   # 2 repeats × (2 in kept, 2 out dropped)
        self.assertEqual(d1["recall"], 1.0)
        self.assertEqual(h["stability"]["unanimous_share"], 1.0)
        self.assertEqual(h["stability"]["fleiss_kappa"], 1.0)
        self.assertEqual(h["borderline_kept_share"], [1.0, 1.0])
        self.assertEqual(h["grounding"]["G1_parsed"], 1.0)
        self.assertEqual(h["grounding"]["G3_evidence_verbatim"], 1.0)
        self.assertEqual(set(h["e2e"]), {"haiku", "sonnet46"})
        self.assertEqual(h["e2e"]["sonnet46"]["D"]["micro"]["recall"], 1.0)
        self.assertEqual(s["paired"][0]["recall_diff_b_minus_a"], 0.0)
        self.assertFalse(s["H1"])
        self.assertEqual(s["template_hash_mismatch"], [])


if __name__ == "__main__":
    unittest.main()
