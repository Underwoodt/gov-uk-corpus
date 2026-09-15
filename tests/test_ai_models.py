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

    def test_price_grid_stored_and_standard_synced(self):
        prices = {"in_hit_off": 0.003, "in_hit_peak": 0.006,
                  "in_miss_off": 0.15, "in_miss_peak": 0.30,
                  "out_off": 0.60, "out_peak": 1.20}
        mid = ai_models.add_model(self.conn, "deepseek", "deepseek-chat", prices=prices)
        row = ai_models.get_model(self.conn, mid)
        for f, v in prices.items():
            self.assertAlmostEqual(row[f], v, msg=f)
        # Standard rate the cost engine reads = cache-miss off-peak / output off-peak.
        self.assertAlmostEqual(row["input_per_m"], 0.15)
        self.assertAlmostEqual(row["output_per_m"], 0.60)

    def test_missing_tiers_default_from_standard(self):
        # Only positional standard rates given -> all tiers fall back to them.
        mid = ai_models.add_model(self.conn, "anthropic", "claude-opus-5", 5.0, 25.0)
        row = ai_models.get_model(self.conn, mid)
        self.assertEqual((row["in_hit_off"], row["in_hit_peak"]), (5.0, 5.0))
        self.assertEqual((row["in_miss_off"], row["in_miss_peak"]), (5.0, 5.0))
        self.assertEqual((row["out_off"], row["out_peak"]), (25.0, 25.0))


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
