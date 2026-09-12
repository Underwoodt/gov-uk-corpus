"""Migration test: old-format SQLite -> new schema (SQLite target).
Run: python3 -m unittest -v tests.test_migrate"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db
from govuk_corpus.migrate_sqlite import migrate


class TestMigrate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src_path = os.path.join(self.tmp, "old.db")
        src = sqlite3.connect(self.src_path)
        src.executescript(
            """
            CREATE TABLE content (url TEXT, content TEXT, fetched_at TEXT, status_code INT,
                                  document_type TEXT, title TEXT, description TEXT, destination_url TEXT);
            CREATE TABLE sitemap (url TEXT, sitemap_file TEXT, lastmod TEXT, imported_at TEXT);
            """
        )
        src.execute("INSERT INTO sitemap (url, sitemap_file, lastmod) VALUES (?,?,?)",
                    ("https://www.gov.uk/a", "s1", "2026-01-01T00:00:00+00:00"))
        src.executemany(
            "INSERT INTO content (url, content, status_code, document_type, title) VALUES (?,?,?,?,?)",
            [
                ("https://www.gov.uk/a", '{"title":"A"}', 200, "guidance", "A"),   # in sitemap
                ("https://gov.uk/b", '{"title":"B"}', 200, "redirect", "B"),        # not in sitemap; bare host
                ("https://gov.ukhttps://x.com/y", "{}", 200, "guidance", "bad"),    # invalid url
            ],
        )
        src.commit(); src.close()

        self.conn = db.connect(os.path.join(self.tmp, "new.db"))
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_migration(self):
        src = sqlite3.connect(f"file:{self.src_path}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
        run_id = db.start_run(self.conn, "migrate", "test")
        counters = migrate(self.conn, src, run_id, db.now_iso())
        src.close()

        self.assertEqual(counters["sitemap_written"], 1)
        self.assertEqual(counters["content_written"], 2)   # a + b; bad url skipped
        self.assertEqual(counters["invalid_url"], 1)

        rows = {r["url"]: r for r in self.conn.execute(
            "SELECT url, source, is_redirect, content_hash, sitemap_lastmod FROM content")}
        # bare-host b canonicalised to www
        self.assertIn("https://www.gov.uk/b", rows)
        # a is in the sitemap -> source sitemap, carries lastmod
        self.assertEqual(rows["https://www.gov.uk/a"]["source"], "sitemap")
        self.assertEqual(rows["https://www.gov.uk/a"]["sitemap_lastmod"], "2026-01-01T00:00:00+00:00")
        # b not in sitemap -> other; redirect flagged
        self.assertEqual(rows["https://www.gov.uk/b"]["source"], "other")
        self.assertEqual(rows["https://www.gov.uk/b"]["is_redirect"], 1)
        # hashes computed
        self.assertTrue(all(r["content_hash"] for r in rows.values()))


if __name__ == "__main__":
    unittest.main()
