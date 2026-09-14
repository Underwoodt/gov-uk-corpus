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
        self.assertEqual(r.gds_issue_count("We will pay you within 5 days. You do not need to apply."), 0)

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

        a = conn.execute("SELECT reading_age, gds_english_score, gds_findings FROM content WHERE url='https://www.gov.uk/a'").fetchone()
        self.assertIsNotNone(a["reading_age"])
        self.assertGreaterEqual(a["gds_english_score"], 2)   # utilise, in order to, leverage
        self.assertIn("utilise", a["gds_findings"])          # findings text describes the flags
        b = conn.execute("SELECT reading_age, gds_english_score FROM content WHERE url='https://www.gov.uk/b'").fetchone()
        self.assertIsNone(b["reading_age"])
        self.assertEqual(b["gds_english_score"], 0)

        # re-run finds nothing to do (marked done via gds_english_score)
        again = build(conn)
        self.assertEqual(again["scanned"], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
