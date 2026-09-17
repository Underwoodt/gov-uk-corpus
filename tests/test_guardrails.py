"""Server-side guardrail checks for user-entered text.
Run: python3 -m unittest -v tests.test_guardrails"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import guardrails as g


class TestGuardrails(unittest.TestCase):
    def test_clean_text_passes(self):
        self.assertIsNone(g.check("Guidance from DEFRA and APHA about IPAFFS step-by-step"))
        self.assertIsNone(g.check(""))

    def test_email_is_blocked_and_masked(self):
        f = g.check("owner is jane.doe@defra.gov.uk please")
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "personal_data")
        self.assertIn("email address", f["offending"])
        self.assertNotIn("jane.doe@defra.gov.uk", f["offending"])   # masked, never echoed raw
        msg = g.refusal_message(f)
        self.assertIn("can't use that", msg.lower())
        self.assertNotIn("jane.doe@defra.gov.uk", msg)

    def test_phone_is_blocked_and_masked(self):
        f = g.check("call me on 07123 456789")
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "personal_data")
        self.assertIn("phone number", f["offending"])
        self.assertNotIn("456789", f["offending"])

    def test_prohibited_language_blocked_without_echo(self):
        f = g.check("this is shit guidance")
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "prohibited_language")
        self.assertNotIn("shit", g.refusal_message(f).lower())      # never repeats the term

    def test_word_boundary_avoids_false_positives(self):
        # 'shit' must not match inside a larger word (Scunthorpe problem).
        self.assertIsNone(g.check("Guidance on the assassination inquiry and shitake mushrooms"))

    def test_extra_terms_argument(self):
        self.assertIsNone(g.check("mention of widget"))
        self.assertIsNotNone(g.check("mention of widget", extra_terms=["widget"]))

    def test_sensitive_topic_is_allowed(self):
        # A sensitive subject with no PII or abuse must pass.
        self.assertIsNone(g.check("guidance about race equality and discrimination law"))


if __name__ == "__main__":
    unittest.main()
