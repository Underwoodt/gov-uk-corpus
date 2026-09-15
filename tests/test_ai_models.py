"""AI model registry tests.
Run: python3 -m unittest -v tests.test_ai_models"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, ai_models


class TestAiModels(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_seed_defaults_once(self):
        ai_models.seed_defaults(self.conn)
        n = len(ai_models.list_models(self.conn))
        self.assertGreaterEqual(n, 2)
        ai_models.seed_defaults(self.conn)               # idempotent
        self.assertEqual(len(ai_models.list_models(self.conn)), n)

    def test_add_find_delete(self):
        mid = ai_models.add_model(self.conn, "anthropic", "claude-opus-5", 5.0, 25.0)
        row = ai_models.find(self.conn, "anthropic", "claude-opus-5")
        self.assertEqual((row["input_per_m"], row["output_per_m"]), (5.0, 25.0))
        ai_models.delete_model(self.conn, mid)
        self.assertIsNone(ai_models.find(self.conn, "anthropic", "claude-opus-5"))

    def test_update_model(self):
        mid = ai_models.add_model(self.conn, "anthropic", "claude-sonnet-5", 2.0, 10.0)
        ai_models.update_model(self.conn, mid, "anthropic", "claude-sonnet-5", 3.0, 15.0)
        row = ai_models.get_model(self.conn, mid)
        self.assertEqual((row["input_per_m"], row["output_per_m"]), (3.0, 15.0))


@unittest.skipUnless(__import__("importlib").util.find_spec("fastapi"), "no fastapi")
class TestConfigResolution(unittest.TestCase):
    def test_active_model_drives_prices(self):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["CORPUS_DB"] = path
        os.environ.pop("DB_HOST", None)
        import importlib
        import webapp.app as app
        importlib.reload(app)
        from govuk_corpus import settings as st
        conn = app.connect()
        app.db.init_db(conn)
        mid = ai_models.add_model(conn, "deepseek", "deepseek-chat", 0.27, 1.10)
        st.set_setting(conn, "active_model_id", str(mid))
        conn.commit(); conn.close()

        conn = app.connect()
        cfg = app._ai_config(conn)
        self.assertEqual(cfg["provider"], "deepseek")
        self.assertEqual(cfg["model"], "deepseek-chat")
        self.assertEqual(cfg["price_in"], 0.27)
        self.assertEqual(cfg["price_out"], 1.10)
        conn.close()
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
