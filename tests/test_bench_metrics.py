"""govuk_corpus.bench_metrics: pure metric functions. No DB.
Run: python3 -m unittest -v tests.test_bench_metrics"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import bench_metrics as bm

GOLD = {"a": "in", "b": "in", "c": "out", "d": "out", "e": "borderline", "f": "in"}
V = {"a": 1, "b": 0, "c": 1, "d": 0, "e": 1, "f": None}     # f unparseable


class TestConfusion(unittest.TestCase):
    def test_counts_on_definite_set(self):
        r = bm.phase_metrics(GOLD, V, "D")
        self.assertEqual((r["tp"], r["fp"], r["fn"], r["tn"], r["n"], r["unparseable"]), (1, 1, 2, 1, 5, 1))
        self.assertAlmostEqual(r["precision"], 0.5)
        self.assertAlmostEqual(r["recall"], 1 / 3)
        self.assertAlmostEqual(r["specificity"], 0.5)

    def test_borderline_bounds(self):
        up = bm.phase_metrics(GOLD, V, "borderline_in")      # e counts as in and was kept -> tp
        self.assertEqual((up["tp"], up["n"]), (2, 6))
        down = bm.phase_metrics(GOLD, V, "borderline_out")   # e counts as out and was kept -> fp
        self.assertEqual((down["fp"], down["n"]), (2, 6))
        self.assertEqual(bm.borderline_agreement(GOLD, V), 1.0)

    def test_empty_rates_are_none(self):
        r = bm.rates(bm.confusion([]))
        self.assertIsNone(r["precision"])
        self.assertIsNone(r["recall"])


class TestPhase2(unittest.TestCase):
    def test_end_to_end_and_value_add(self):
        p1 = {"a": 1, "b": 1, "c": 1, "d": 0, "e": 1, "f": 1}
        p2 = {"a": 1, "b": 0, "c": 0, "e": 1}                  # f not reached -> stays kept
        e = bm.end_to_end(p1, p2)
        self.assertEqual(e, {"a": 1, "b": 0, "c": 0, "d": 0, "e": 1, "f": 1})
        va = bm.phase2_value_add(GOLD, p1, p2, "D")
        # e is borderline -> outside D, so 4 Phase-1 keeps count (a, b, c, f)
        self.assertEqual((va["p1_keeps"], va["fp_removed"], va["tp_wrongly_dropped"], va["net"]), (4, 1, 1, 0))
        self.assertAlmostEqual(va["drop_precision"], 0.5)
        self.assertEqual(bm.end_to_end(p1, None), p1)


class TestStability(unittest.TestCase):
    def test_all_agree_kappa_one(self):
        reps = [{"a": 1, "b": 0}] * 5
        s = bm.stability(reps, ["a", "b"])
        self.assertEqual(s["unanimous_share"], 1.0)
        self.assertEqual(s["flip_pages"], [])
        self.assertEqual(s["fleiss_kappa"], 1.0)

    def test_fleiss_textbook(self):
        # Fleiss (1971) worked example: 10 subjects, 14 raters, 5 categories -> kappa 0.210
        rows = [(0, 0, 0, 0, 14), (0, 2, 6, 4, 2), (0, 0, 3, 5, 6), (0, 3, 9, 2, 0), (2, 2, 8, 1, 1),
                (7, 7, 0, 0, 0), (3, 2, 6, 3, 0), (2, 5, 3, 2, 2), (6, 5, 2, 1, 0), (0, 2, 2, 3, 7)]
        self.assertAlmostEqual(bm.fleiss_kappa(rows), 0.2099, places=3)
        self.assertIsNone(bm.fleiss_kappa([]))
        self.assertIsNone(bm.fleiss_kappa([(1, 0)]))          # one rater

    def test_flips_and_majority(self):
        reps = [{"a": 1, "b": 1}, {"a": 1, "b": 0}, {"a": 1, "b": 0}, {"a": 0, "b": 1}, {"a": 1, "b": None}]
        s = bm.stability(reps, ["a", "b"])
        self.assertEqual(s["flip_pages"], ["a", "b"])
        self.assertEqual(bm.majority_vote(reps, ["a", "b"]), {"a": 1, "b": 0})
        self.assertEqual(bm.majority_vote([{"a": None}], ["a"]), {"a": None})

    def test_score_stability(self):
        sc = [{"a": 0.9, "b": 0.1}, {"a": 0.8, "b": 0.5}, {"a": 0.9, "b": 0.2}]
        band = lambda v: "hi" if v > 0.65 else "lo"
        s = bm.score_stability(sc, ["a", "b"], band_fn=band)
        self.assertEqual(s["pages"], 2)
        self.assertEqual(s["band_stable_share"], 1.0)
        self.assertGreater(s["score_sd_over_threshold_share"], 0)


class TestCalibration(unittest.TestCase):
    def test_bands_and_monotone(self):
        gold = {"a": "in", "b": "in", "c": "out", "d": "out", "e": "in"}
        scores = {"a": 0.9, "b": 0.5, "c": 0.0, "d": 0.2, "e": 0.8}
        band = lambda s: "0" if not s else ("0.1-0.3" if s <= 0.35 else ("0.4-0.6" if s <= 0.65 else "0.7-1.0"))
        c = bm.calibration(gold, scores, band)
        self.assertEqual(c["bands"]["0.7-1.0"]["gold_in_share"], 1.0)
        self.assertEqual(c["bands"]["0"]["gold_in_share"], 0.0)
        self.assertTrue(c["monotone"])
        self.assertIsNotNone(c["brier"])


class TestGrounding(unittest.TestCase):
    PAGE = {"title": "Inheritance Tax: “thresholds”", "description": "How  much you pay",
            "body": "Inheritance Tax is\n  paid on estates over £325,000. It’s charged at 40%."}

    def test_verbatim_with_curly_quotes_and_whitespace(self):
        row = {"keep": 1, "score": 0.8, "evidence": ["inheritance tax: \"thresholds\"", "It's charged at 40%."],
               "where_hit": ["title", "body"], "primary_topic": "Inheritance Tax thresholds and rates"}
        g = bm.grounding_checks(row, self.PAGE, include_text="Inheritance Tax rules. More text.")
        self.assertTrue(g["G1_parsed"] and g["G2_keep_matches_score"] and g["G3_evidence_verbatim"])
        self.assertTrue(g["G4_where_valid"] and g["G5_topic_sane"] and g["G6_score_in_range"] and g["G7_not_truncated"])

    def test_failures(self):
        row = {"keep": 1, "score": 0.0, "evidence": ["not in the page"], "where_hit": ["footer"],
               "primary_topic": " ".join(["w"] * 11)}
        g = bm.grounding_checks(row, self.PAGE, max_tokens_hit=True)
        self.assertFalse(g["G2_keep_matches_score"])
        self.assertFalse(g["G3_evidence_verbatim"])
        self.assertFalse(g["G4_where_valid"])
        self.assertFalse(g["G5_topic_sane"])
        self.assertFalse(g["G7_not_truncated"])
        # a drop has no evidence obligation
        g2 = bm.grounding_checks({"keep": 0, "score": 0.0, "primary_topic": "Something"}, self.PAGE)
        self.assertIsNone(g2["G3_evidence_verbatim"])
        self.assertTrue(g2["G2_keep_matches_score"])
        # topic that just restates the INCLUDE text fails G5
        g3 = bm.grounding_checks({"keep": 0, "score": 0.0, "primary_topic": "Inheritance Tax rules"},
                                 self.PAGE, include_text="Inheritance Tax rules. More.")
        self.assertFalse(g3["G5_topic_sane"])
        un = bm.grounding_checks({"keep": None, "score": None, "parsed": False}, self.PAGE)
        self.assertFalse(un["G1_parsed"])
        self.assertIsNone(un["G2_keep_matches_score"])

    def test_pass_rates(self):
        rows = [{"G1": True, "G3": None}, {"G1": False, "G3": True}]
        self.assertEqual(bm.pass_rates(rows), {"G1": 0.5, "G3": 1.0})


class TestPaired(unittest.TestCase):
    def test_mcnemar_known_values(self):
        self.assertIsNone(bm.mcnemar_exact(0, 0))
        self.assertAlmostEqual(bm.mcnemar_exact(5, 0), 2 * (1 / 32))        # 0.0625
        self.assertAlmostEqual(bm.mcnemar_exact(3, 3), 1.0)
        self.assertAlmostEqual(bm.mcnemar_exact(8, 1), 2 * (1 + 9) / 512)   # 0.0390625

    def test_discordant_and_kappa(self):
        a = {"a": 1, "b": 1, "c": 1, "d": 0}
        b = {"a": 1, "b": 0, "c": 0, "d": 0}
        self.assertEqual(bm.discordant(GOLD, a, b, "D"), (1, 1))   # b: a right; c: b right
        self.assertEqual(bm.cohens_kappa(a, a, list(a)), 1.0)
        self.assertLess(bm.cohens_kappa(a, b, list(a)), 1.0)
        self.assertAlmostEqual(bm.cohens_h(0.5, 0.5), 0.0)

    def test_bootstrap_is_seeded_and_brackets_point(self):
        gold = {f"u{i}": ("in" if i % 3 else "out") for i in range(60)}
        va = {u: (1 if int(u[1:]) % 4 else 0) for u in gold}
        vb = {u: (1 if int(u[1:]) % 5 else 0) for u in gold}
        r1 = bm.bootstrap_ci(gold, va, metric="recall", B=300)
        r2 = bm.bootstrap_ci(gold, va, metric="recall", B=300)
        self.assertEqual((r1["lo"], r1["hi"]), (r2["lo"], r2["hi"]))
        self.assertLessEqual(r1["lo"], r1["point"])
        self.assertGreaterEqual(r1["hi"], r1["point"])
        d = bm.bootstrap_ci(gold, va, vb, metric="recall", B=300)
        self.assertAlmostEqual(d["point"], bm.phase_metrics(gold, va)["recall"] - bm.phase_metrics(gold, vb)["recall"])

    def test_percentile_and_cost(self):
        self.assertEqual(bm.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertIsNone(bm.percentile([], 0.5))
        c = bm.cost_efficiency(1.0, {"tp": 2, "tn": 2})
        self.assertEqual(c["cost_per_correct_decision"], 0.25)
        self.assertEqual(c["cost_per_gold_in_recalled"], 0.5)


if __name__ == "__main__":
    unittest.main()


class TestWeighted(unittest.TestCase):
    def test_weighted_confusion_counts_inverse_probability(self):
        gold = {"k1": "in", "k2": "out", "d1": "in", "d2": "out"}
        v = {"k1": 1, "k2": 1, "d1": 0, "d2": 0}
        # keeps sampled fully (w=1), drops sampled at 1/3 (w=3): the missed 'in' counts three times
        w = {"k1": 1.0, "k2": 1.0, "d1": 3.0, "d2": 3.0}
        raw = bm.phase_metrics(gold, v)
        wt = bm.phase_metrics_weighted(gold, v, w)
        self.assertAlmostEqual(raw["recall"], 0.5)
        self.assertAlmostEqual(wt["recall"], 1 / 4)            # tp 1 vs fn 3
        self.assertEqual(wt["pages"], 4)
        self.assertAlmostEqual(wt["n"], 8.0)
        self.assertAlmostEqual(wt["specificity"], 3 / 4)
        # all weights 1 -> identical to the raw figures
        same = bm.phase_metrics_weighted(gold, v, {})
        self.assertEqual((same["recall"], same["precision"]), (raw["recall"], raw["precision"]))
