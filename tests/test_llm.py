"""govuk_corpus.llm: the request builder (pure) and the sampling-capability guard. No network.
Run: python3 -m unittest -v tests.test_llm"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import llm

CFG = {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "key": "k", "base_url": "",
       "label": "Anthropic", "price_in": 1.0, "price_out": 5.0}
MSGS = [{"role": "user", "content": "hi"}]


class TestRequestKwargs(unittest.TestCase):
    def test_defaults_send_no_sampling_controls(self):
        kw = llm.request_kwargs(CFG, "", MSGS, 4096)
        self.assertEqual(set(kw), {"model", "max_tokens", "messages"})
        self.assertEqual(kw["max_tokens"], 4096)

    def test_temperature_only_when_given(self):
        kw = llm.request_kwargs(CFG, "sys", MSGS, 100, temperature=0)
        self.assertEqual(kw["temperature"], 0.0)
        self.assertEqual(kw["system"], "sys")
        self.assertNotIn("thinking", kw)

    def test_system_cache_control_only_on_anthropic(self):
        kw = llm.request_kwargs(CFG, "sys", MSGS, 100, cache_system=True)
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        ds = dict(CFG, provider="deepseek", model="deepseek-chat")
        self.assertEqual(llm.request_kwargs(ds, "sys", MSGS, 100, cache_system=True)["system"], "sys")

    def test_thinking_and_effort_only_for_claude_providers(self):
        kw = llm.request_kwargs(CFG, "", MSGS, 100, thinking={"type": "adaptive"}, effort="low")
        self.assertEqual(kw["thinking"], {"type": "adaptive"})
        self.assertEqual(kw["output_config"], {"effort": "low"})
        ds = dict(CFG, provider="deepseek", model="deepseek-chat")
        kw = llm.request_kwargs(ds, "", MSGS, 100, thinking={"type": "adaptive"}, effort="low")
        self.assertNotIn("thinking", kw)
        self.assertNotIn("output_config", kw)
        br = dict(CFG, provider="bedrock", model="eu.anthropic.claude-haiku-4-5-20251001-v1:0")
        self.assertIn("thinking", llm.request_kwargs(br, "", MSGS, 100, thinking={"type": "adaptive"}))

    def test_temperature_refused_on_models_that_reject_it(self):
        for m in ("claude-sonnet-5", "claude-opus-5", "claude-opus-4-7", "claude-fable-5-1",
                  "eu.anthropic.claude-sonnet-5-v1:0"):
            self.assertFalse(llm.supports_sampling(m), m)
            with self.assertRaises(ValueError):
                llm.request_kwargs(dict(CFG, model=m), "", MSGS, 100, temperature=0)
            # …but without a temperature the request is fine.
            llm.request_kwargs(dict(CFG, model=m), "", MSGS, 100)
        for m in ("claude-haiku-4-5-20251001", "claude-sonnet-4-6", "deepseek-chat"):
            self.assertTrue(llm.supports_sampling(m), m)

    def test_chat_reports_refused_temperature_as_fatal(self):
        res = llm.chat(dict(CFG, model="claude-sonnet-5"), "", MSGS, 10, temperature=0)
        self.assertTrue(res.get("fatal"))
        self.assertIn("temperature", res["error"])

    def test_chat_without_key_is_fatal(self):
        res = llm.chat(dict(CFG, key=None), "", MSGS, 10)
        self.assertTrue(res.get("fatal"))


class TestReply(unittest.TestCase):
    def test_reply_passes_sampling_through(self):
        seen = {}

        def fake_chat(config, system, messages, max_tokens=1024, cache_system=False, **kw):
            seen.update(kw)
            seen["max_tokens"] = max_tokens
            return {"reply": "{}"}
        orig = llm.chat
        llm.chat = fake_chat
        try:
            llm.reply(CFG, "", "p", sampling={"temperature": 0.0, "thinking": None, "effort": None})
        finally:
            llm.chat = orig
        self.assertEqual(seen["temperature"], 0.0)
        self.assertIsNone(seen["thinking"])
        self.assertEqual(seen["max_tokens"], llm.eval_max_tokens())


if __name__ == "__main__":
    unittest.main()
