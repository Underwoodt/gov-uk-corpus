"""prompt-fields backfill tests.
Run: python3 -m unittest -v tests.test_backfill_prompt_fields"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, categories as cat, backfill_prompt_fields as bf


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        self.cid = cat.create_category(self.conn, {
            "slug": "slurry", "owner_email": "a@b.co", "description": "Slurry",
            "inclusion_context": "slurry storage", "exclusion_context": "sewage sludge",
            "adjudication_hints_keep": "grant to fund a store",
            "adjudication_hints_drop": "biosolids not animal slurry"})
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _run(self, run_id, prompt_spec):
        self.conn.execute(
            "INSERT INTO evaluation_runs (run_id, category_id, name, phase, model, provider, started_at, prompt_spec) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, self.cid, run_id, "inclusion", "m", "anthropic", db.now_iso(), prompt_spec))
        self.conn.commit()

    def _spec(self, run_id):
        row = self.conn.execute("SELECT prompt_spec FROM evaluation_runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row["prompt_spec"]) if row["prompt_spec"] else None

    def test_null_spec_filled_from_current_category(self):
        self._run("r_null", None)
        counters = bf.backfill(self.conn)
        self.assertEqual(counters["backfilled"], 1)
        spec = self._spec("r_null")
        self.assertEqual(spec["inclusion_context"], "slurry storage")
        self.assertEqual(spec["exclusion_context"], "sewage sludge")
        self.assertEqual(spec["keep_hints"], "grant to fund a store")
        self.assertEqual(spec["drop_hints"], "biosolids not animal slurry")
        self.assertEqual(spec["name"], "Slurry")
        self.assertTrue(spec["criteria_backfilled"])
        self.assertEqual(spec["criteria_backfilled_from"], "category_current")
        self.assertIn("inclusion_context", spec["criteria_backfilled_keys"])

    def test_genuine_values_never_overwritten(self):
        # A run stamped with the real historical wording — even an empty drop_hints must survive.
        self._run("r_stamped", json.dumps({
            "inclusion_context": "OLD include", "exclusion_context": "OLD exclude",
            "keep_hints": "OLD keep", "drop_hints": "", "name": "Old topic"}))
        bf.backfill(self.conn)
        spec = self._spec("r_stamped")
        self.assertEqual(spec["inclusion_context"], "OLD include")
        self.assertEqual(spec["drop_hints"], "")          # present-but-empty is genuine, kept
        self.assertNotIn("criteria_backfilled", spec)      # not touched at all

    def test_partial_spec_fills_only_absent_keys(self):
        self._run("r_partial", json.dumps({"inclusion_context": "OLD include", "name": "Old"}))
        bf.backfill(self.conn)
        spec = self._spec("r_partial")
        self.assertEqual(spec["inclusion_context"], "OLD include")   # kept
        self.assertEqual(spec["name"], "Old")                        # kept
        self.assertEqual(spec["exclusion_context"], "sewage sludge") # filled
        self.assertEqual(spec["keep_hints"], "grant to fund a store")
        self.assertEqual(sorted(spec["criteria_backfilled_keys"]),
                         ["drop_hints", "exclusion_context", "keep_hints"])

    def test_idempotent(self):
        self._run("r_null", None)
        first = bf.backfill(self.conn)
        second = bf.backfill(self.conn)
        self.assertEqual(first["backfilled"], 1)
        self.assertEqual(second["backfilled"], 0)      # nothing left to do
        self.assertEqual(second["scanned"], 0)         # pre-filtered out by SQL

    def test_missing_category_left_alone(self):
        self._run("r_orphan", None)
        self.conn.execute("UPDATE evaluation_runs SET category_id=999999 WHERE run_id='r_orphan'")
        self.conn.commit()
        counters = bf.backfill(self.conn)
        self.assertEqual(counters["no_category"], 1)
        self.assertEqual(counters["backfilled"], 0)
        self.assertIsNone(self._spec("r_orphan"))

    def test_runs_via_init_db(self):
        # A NULL-spec run present before init_db is backfilled by init_db itself (the deploy path).
        self._run("r_deploy", None)
        db.init_db(self.conn)
        spec = self._spec("r_deploy")
        self.assertTrue(spec["criteria_backfilled"])
        self.assertEqual(spec["inclusion_context"], "slurry storage")


if __name__ == "__main__":
    unittest.main()
