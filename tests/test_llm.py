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

    def test_system_cache_control_on_anthropic_and_foundry(self):
        kw = llm.request_kwargs(CFG, "sys", MSGS, 100, cache_system=True)
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        fo = dict(CFG, provider="foundry", model="claude-haiku-4-5")
        self.assertEqual(llm.request_kwargs(fo, "sys", MSGS, 100, cache_system=True)["system"][0]["cache_control"],
                         {"type": "ephemeral"})
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
        fo = dict(CFG, provider="foundry", model="claude-haiku-4-5")
        kw = llm.request_kwargs(fo, "", MSGS, 100, thinking={"type": "adaptive"}, effort="low")
        self.assertEqual(kw["thinking"], {"type": "adaptive"})
        self.assertEqual(kw["output_config"], {"effort": "low"})

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


_FOUNDRY_ENVS = ("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE",
                 "ANTHROPIC_FOUNDRY_BASE_URL")


def _has_azure_identity() -> bool:
    try:
        return __import__("importlib").util.find_spec("azure.identity") is not None
    except ImportError:   # parent `azure` namespace itself missing
        return False


class TestFoundryCreds(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _FOUNDRY_ENVS}
        for k in _FOUNDRY_ENVS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_unconfigured_without_endpoint(self):
        self.assertIsNone(llm.foundry_creds())
        self.assertFalse(llm.provider_configured("foundry"))
        os.environ["ANTHROPIC_FOUNDRY_API_KEY"] = "k"   # key alone is not enough
        self.assertIsNone(llm.foundry_creds())

    def test_key_plus_resource(self):
        os.environ["ANTHROPIC_FOUNDRY_API_KEY"] = "k"
        os.environ["ANTHROPIC_FOUNDRY_RESOURCE"] = "res"
        creds = llm.foundry_creds()
        self.assertEqual((creds["api_key"], creds["resource"]), ("k", "res"))
        self.assertTrue(llm.provider_configured("foundry"))

    def test_base_url_alternative_to_resource(self):
        os.environ["ANTHROPIC_FOUNDRY_API_KEY"] = "k"
        os.environ["ANTHROPIC_FOUNDRY_BASE_URL"] = "https://res.services.ai.azure.com/anthropic/"
        creds = llm.foundry_creds()
        self.assertEqual(creds["base_url"], "https://res.services.ai.azure.com/anthropic")

    def test_chat_without_foundry_config_is_fatal(self):
        fo = dict(CFG, provider="foundry", model="claude-haiku-4-5", key=None)
        res = llm.chat(fo, "", MSGS, 10)
        self.assertTrue(res.get("fatal"))

    @unittest.skipUnless(_has_azure_identity(), "no azure-identity")
    def test_entra_when_keyless_with_endpoint(self):
        os.environ["ANTHROPIC_FOUNDRY_RESOURCE"] = "res"
        creds = llm.foundry_creds()
        self.assertTrue(creds.get("use_entra"))
        self.assertTrue(llm.provider_configured("foundry"))


if __name__ == "__main__":
    unittest.main()
