"""Version-keyed funnel cache tests.
Run: python3 -m unittest -v tests.test_funnel_cache"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import fastapi  # noqa: F401
    _HAS_WEBAPP = True
except Exception:
    _HAS_WEBAPP = False


@unittest.skipUnless(_HAS_WEBAPP, "web app deps (fastapi) not installed")
class TestFunnelCache(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        os.environ["CORPUS_DB"] = self.path
        os.environ.pop("DB_HOST", None)
        import importlib
        import webapp.app as app
        importlib.reload(app)
        self.app = app
        conn = app.connect(); app.db.init_db(conn); conn.close()

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_def_version_ignores_non_filter_fields(self):
        a = {"dept_slugs": "ea", "document_type_slugs": "guidance", "keywords": "slurry",
             "include_child_orgs": 1, "owner_email": "x@y", "inclusion_context": "foo"}
        b = dict(a, owner_email="z@y", inclusion_context="different")   # non-filter edits
        c = dict(a, keywords="slurry, manure")                          # filter edit
        self.assertEqual(self.app._def_version(a), self.app._def_version(b))
        self.assertNotEqual(self.app._def_version(a), self.app._def_version(c))

    def test_cache_round_trip_and_version_invalidation(self):
        app = self.app
        conn = app.connect()
        app._funnel_cache_write(conn, 7, "defA", "corpX", "doctype", 42)
        self.assertEqual(app._funnel_cache_read(conn, 7, "defA", "corpX"), {"doctype": 42})
        # a different definition or corpus version => miss
        self.assertEqual(app._funnel_cache_read(conn, 7, "defB", "corpX"), {})
        self.assertEqual(app._funnel_cache_read(conn, 7, "defA", "corpY"), {})
        # writing under a new version resets the blob (old stage dropped)
        app._funnel_cache_write(conn, 7, "defA", "corpY", "org", 10)
        self.assertEqual(app._funnel_cache_read(conn, 7, "defA", "corpY"), {"org": 10})
        conn.close()

    def test_corpus_version_tracks_runs(self):
        app = self.app
        conn = app.connect()
        self.assertEqual(app._corpus_version(conn), "")
        conn.execute("INSERT INTO runs (run_id, started_at, finished_at, status) "
                     "VALUES ('r1','t','2030-01-01','complete')")
        conn.commit()
        self.assertEqual(app._corpus_version(conn), "2030-01-01")
        conn.close()


if __name__ == "__main__":
    unittest.main()
