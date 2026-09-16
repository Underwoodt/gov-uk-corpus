"""Readability + GDS analysis tests.
Run: python3 -m unittest -v tests.test_readability"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import readability as r
from govuk_corpus.build_readability import build
from govuk_corpus import db


class TestReadability(unittest.TestCase):
    def test_short_text_has_no_grade(self):
        self.assertIsNone(r.reading_age("Too short."))

    def test_simple_text_lower_age_than_complex(self):
        simple = ("The cat sat on the mat. The dog ran to the park. "
                  "We had a good day. It was fun to play outside. Birds can fly.")
        complex_ = ("The aforementioned methodology necessitates comprehensive "
                    "reconceptualisation of institutional frameworks, "
                    "notwithstanding considerable epistemological uncertainties "
                    "regarding the underlying theoretical presuppositions herein.")
        self.assertLess(r.reading_age(simple), r.reading_age(complex_))

    def test_reading_age_floor(self):
        self.assertGreaterEqual(r.reading_age("The cat sat on the mat and had fun today."), 5.0)

    def test_gds_flags_avoid_words_and_phrases(self):
        text = ("We will utilise this in order to leverage best practice "
                "and facilitate delivery.")
        # utilise, in order to, leverage, best practice, facilitate = 5
        self.assertGreaterEqual(r.gds_issue_count(text), 5)

    def test_gds_clean_text_scores_zero(self):
        # No avoid-words/phrases, no we/us/our, no vague/nominalised terms.
        self.assertEqual(r.gds_issue_count("Send the form within 5 days. You can pay online."), 0)

    def test_gds_flags_long_sentence(self):
        long = " ".join(["word"] * 40) + "."
        self.assertGreaterEqual(r.gds_issue_count(long), 1)

    def test_empty_text(self):
        self.assertEqual(r.gds_issue_count(""), 0)
        self.assertIsNone(r.reading_age(""))

    def test_findings_text_lists_flags_and_agrees_with_count(self):
        text = "We will utilise this in order to leverage best practice."
        findings = r.gds_findings(text)
        self.assertIn("Words to avoid", findings)
        self.assertIn("Phrases to avoid", findings)
        self.assertEqual(r.gds_findings(""), "")   # clean/empty -> no findings
        self.assertEqual(r.gds_findings("Pay within 5 days."), "")

    def test_scan_counts_new_classes(self):
        s = r.scan("The implementation and completion of the assessment. "
                   "Some pages are often updated. We will help you. "
                   "The applicant must apply. You must not wait. It is required that you attend.")
        c = s["counts"]
        self.assertGreaterEqual(c.get("nominalisation", 0), 3)
        self.assertGreaterEqual(c.get("vague_language", 0), 2)
        self.assertGreaterEqual(c.get("gov_focused", 0), 1)
        self.assertGreaterEqual(c.get("applicant", 0), 1)
        self.assertGreaterEqual(c.get("negative_phrasing", 0), 1)
        self.assertGreaterEqual(c.get("impersonal_it_is", 0), 1)

    def test_it_is_narrowed_to_templates(self):
        self.assertEqual(r.scan("It is nice today. It is sunny.")["counts"].get("impersonal_it_is", 0), 0)
        self.assertGreaterEqual(r.scan("It is required that you pay.")["counts"].get("impersonal_it_is", 0), 1)

    def test_impact_weights_severity_over_volume(self):
        heavy = r.scan("The implementation. The completion.")   # 2 nominalisations (weight 3)
        light = r.scan("There is. There are. There is. There are. There is. There are.")  # 6 (weight 1)
        self.assertGreater(heavy["impact"], light["impact"])

    def test_stars_clean_beats_poor(self):
        clean = "The form is clear. You can send it today. " * 6   # short sentences, no flags
        poor = clean + " " + "The implementation is done. The completion is done. " * 6
        self.assertEqual(r.scan(clean)["stars"], 5)
        self.assertLess(r.scan(poor)["stars"], 5)

    def test_stars_none_for_short_text(self):
        self.assertIsNone(r.scan("Short and clean.")["stars"])


class TestBackfill(unittest.TestCase):
    def test_backfill_writes_and_marks_done(self):
        conn = db.connect(":memory:")
        db.init_db(conn)
        rows = [
            ("https://www.gov.uk/a", "We will utilise this in order to leverage synergies across teams and stakeholders daily."),
            ("https://www.gov.uk/b", ""),          # empty -> gds 0, reading_age NULL
        ]
        for url, text in rows:
            conn.execute("INSERT INTO content (url, content_hash, search_text) VALUES (?,?,?)",
                         (url, "h", text))
        conn.commit()

        counters = build(conn)
        self.assertEqual(counters["scanned"], 2)

        a = conn.execute("SELECT reading_age, gds_english_score, gds_findings, gds_checks FROM content WHERE url='https://www.gov.uk/a'").fetchone()
        self.assertIsNotNone(a["reading_age"])
        self.assertGreaterEqual(a["gds_english_score"], 2)   # weighted impact of the flags
        self.assertIn("Words to avoid", a["gds_findings"])   # class-level summary
        self.assertIsNotNone(a["gds_checks"])                # raw per-class JSON stored
        b = conn.execute("SELECT reading_age, gds_english_score FROM content WHERE url='https://www.gov.uk/b'").fetchone()
        self.assertIsNone(b["reading_age"])
        self.assertEqual(b["gds_english_score"], 0)

        # re-run finds nothing to do (marked done via gds_english_score)
        again = build(conn)
        self.assertEqual(again["scanned"], 0)
        conn.close()

    def test_poison_row_is_isolated_and_not_retried(self):
        import govuk_corpus.build_readability as br
        conn = db.connect(":memory:")
        db.init_db(conn)
        for url, text in [("https://www.gov.uk/good", "The cat sat on the mat and had a good day today."),
                          ("https://www.gov.uk/bad", "BOOM")]:
            conn.execute("INSERT INTO content (url, content_hash, search_text) VALUES (?,?,?)",
                         (url, "h", text))
        conn.commit()

        real = br.analyse
        def flaky(text):
            if text == "BOOM":
                raise RuntimeError("kaboom")
            return real(text)
        br.analyse = flaky
        try:
            counters = br.build(conn)          # must NOT raise despite the poison row
        finally:
            br.analyse = real

        self.assertEqual(counters["scanned"], 2)
        self.assertEqual(counters["errors"], 1)
        bad = conn.execute("SELECT gds_english_score, gds_findings FROM content "
                            "WHERE url='https://www.gov.uk/bad'").fetchone()
        self.assertEqual(bad["gds_english_score"], 0)          # marked done
        self.assertIn("analysis error", bad["gds_findings"])
        # re-run does not revisit the poison row
        self.assertEqual(br.build(conn)["scanned"], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
