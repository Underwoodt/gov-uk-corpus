"""Gold-label store tests (govuk_corpus.gold): stratified export, validated import, status.
Run: python3 -m unittest -v tests.test_gold"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from govuk_corpus import db, evaluate, gold

CID = 1789943869717


class GoldBase(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        c = self.conn
        # Keyword shortlist pages p0..p3 (guidance/detailed_guide, keyword "slurry"),
        # plus s1 (GOV.UK-Search-only, above floor) and s2 (below floor); s1/s2 don't
        # match the keyword filter so they only enter via the search top-up.
        pages = [("p0", "guidance", "slurry"), ("p1", "guidance", "slurry"),
                 ("p2", "detailed_guide", "slurry"), ("p3", "detailed_guide", "slurry"),
                 ("s1", "guidance", "nothing"), ("s2", "guidance", "nothing")]
        for slug, dt, body in pages:
            url = f"https://www.gov.uk/{slug}"
            c.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, "
                      "search_text, title, description, content_id) VALUES (?,?,0,?,?,?,?,?)",
                      (url, dt, f"hash-{slug}", body, f"Title {slug}", f"Desc {slug}", f"cid-{slug}"))
            c.execute("INSERT INTO page_organisations (page_url, organisation_content_id, "
                      "organisation_slug, role) VALUES (?,?,?,?)",
                      (url, "environment-agency", "environment-agency", "primary"))
        c.execute("INSERT INTO categories (id, slug, dept_slugs, document_type_slugs, keywords, "
                  "should_include_urls, should_exclude_urls) VALUES (?,?,?,?,?,?,?)",
                  (CID, "inheritance-tax", "environment-agency", "guidance,detailed_guide",
                   "slurry", "https://gov.uk/p0\nhttps://www.gov.uk/p1/", "https://www.gov.uk/p3"))
        for slug, source, es in (("p0", "both", 0.9), ("s1", "search", 0.5), ("s2", "search", 0.001)):
            c.execute("INSERT INTO category_search_pages (category_id, url, source, es_score) "
                      "VALUES (?,?,?,?)", (CID, f"https://www.gov.uk/{slug}", source, es))
        c.commit()

    def tearDown(self):
        self.conn.close()

    def _category(self):
        from govuk_corpus import categories as cat
        return cat.get_category(self.conn, CID)


class TestExport(GoldBase):
    def test_forwarded_set_and_sources(self):
        pages = gold.forwarded_pages(self.conn, self._category())
        by = {p["url"].rsplit("/", 1)[1]: p for p in pages}
        self.assertEqual(sorted(by), ["p0", "p1", "p2", "p3", "s1"])  # s2 is below the floor
        self.assertEqual(by["p0"]["source"], "both")
        self.assertEqual(by["p1"]["source"], "shortlister")
        self.assertEqual(by["s1"]["source"], "search")
        self.assertEqual(by["s1"]["es_score"], 0.5)
        self.assertEqual(by["p2"]["document_type"], "detailed_guide")
        self.assertEqual(by["p0"]["content_hash"], "hash-p0")
        self.assertEqual(by["p0"]["content_id"], "cid-p0")

    def test_below_floor_opt_in(self):
        pages = gold.forwarded_pages(self.conn, self._category(), include_below_floor=True)
        by = {p["url"].rsplit("/", 1)[1]: p for p in pages}
        self.assertIn("s2", by)
        self.assertEqual(by["s2"]["source"], "search_below_floor")

    def test_seed_labels_are_canonicalised(self):
        seeds = gold.seed_labels(self._category())
        self.assertEqual(seeds, {"https://www.gov.uk/p0": "should_include",
                                 "https://www.gov.uk/p1": "should_include",
                                 "https://www.gov.uk/p3": "should_exclude"})

    def test_sheet_carries_latest_verdicts_and_seeds(self):
        run = evaluate.create_run(self.conn, CID, "m", "anthropic")
        evaluate.save_page(self.conn, run, CID, "https://www.gov.uk/p0",
                           {"keep": 1, "score": 0.9, "reason": "core", "primary_topic": "IHT"}, 10)
        evaluate.save_page(self.conn, run, CID, "https://www.gov.uk/p1",
                           {"keep": 0, "score": 0.1, "reason": "passing"}, 10)
        rows = gold.build_sheet(self.conn, CID)
        by = {r["url"]: r for r in rows}
        self.assertEqual(by["https://www.gov.uk/p0"]["p1_keep"], 1)
        self.assertEqual(by["https://www.gov.uk/p0"]["confidence"], "Major focus")
        self.assertEqual(by["https://www.gov.uk/p0"]["primary_topic"], "IHT")
        self.assertEqual(by["https://www.gov.uk/p0"]["seed_label"], "should_include")
        self.assertEqual(by["https://www.gov.uk/p3"]["seed_label"], "should_exclude")
        self.assertEqual(by["https://www.gov.uk/p2"]["confidence"], "")
        self.assertEqual(by["https://www.gov.uk/p2"]["label"], "")  # never pre-filled from seeds
        self.assertEqual([r["order_hint"] for r in rows], list(range(1, len(rows) + 1)))

    def test_stratified_order_is_deterministic_and_covers_strata_first(self):
        rows = [{"url": f"https://www.gov.uk/x{i}", "current_score": s, "source": src,
                 "document_type": dt}
                for i, (s, src, dt) in enumerate(
                    [(0.9, "both", "guidance")] * 5 + [(0.2, "shortlister", "guidance")] * 3
                    + [(None, "search", "detailed_guide")] * 2 + [(0.0, "both", "guidance")])]
        a = gold.stratified_order(rows, seed=42)
        b = gold.stratified_order(list(reversed(rows)), seed=42)
        self.assertEqual([r["url"] for r in a], [r["url"] for r in b])
        strata = {gold.stratum_of(r) for r in rows}
        first = {gold.stratum_of(r) for r in a[:len(strata)]}
        self.assertEqual(first, strata)
        # a page's neighbours within its stratum don't change when another page is added
        more = rows + [{"url": "https://www.gov.uk/new", "current_score": 0.9, "source": "both",
                        "document_type": "guidance"}]
        big_urls = [r["url"] for r in gold.stratified_order(more, seed=42)
                    if gold.stratum_of(r) == (("0.7-1.0", "both", "guidance"))]
        small_urls = [r["url"] for r in a if gold.stratum_of(r) == (("0.7-1.0", "both", "guidance"))]
        self.assertEqual([u for u in big_urls if u != "https://www.gov.uk/new"], small_urls)
        self.assertNotEqual([r["url"] for r in gold.stratified_order(rows, seed=7)],
                            [r["url"] for r in a])

    def test_write_sheet_roundtrip(self):
        rows = gold.build_sheet(self.conn, CID)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sheet.csv")
            gold.write_sheet(rows, path)
            back = gold.read_sheet(path)
        self.assertEqual(len(back), len(rows))
        self.assertEqual(list(back[0].keys()), gold.SHEET_COLUMNS)


class TestImport(GoldBase):
    def _write(self, rows):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "labels.csv")
        gold.write_sheet(rows, path)
        return path

    def _sheet(self, **labels):
        rows = gold.build_sheet(self.conn, CID)
        for r in rows:
            slug = r["url"].rsplit("/", 1)[1]
            if slug in labels:
                r["label"], r["rationale"] = labels[slug]
        return rows

    def test_rejects_bad_label_and_writes_nothing(self):
        rows = self._sheet(p0=("in", "core"), p1=("maybe", "?"))
        with self.assertRaises(ValueError) as cm:
            gold.import_sheet(self.conn, CID, self._write(rows), "tom@example.org")
        self.assertIn("label must be one of", str(cm.exception))
        self.assertEqual(gold.load_gold(self.conn, CID), [])

    def test_rejects_missing_rationale_and_unknown_url(self):
        rows = self._sheet(p0=("in", ""))
        rows.append({"url": "https://www.gov.uk/not-in-corpus", "label": "out", "rationale": "x"})
        rows.append({"url": "https://example.org/x", "label": "out", "rationale": "x"})
        res = gold.validate_sheet(self.conn, CID, gold.read_sheet(self._write(rows)))
        msgs = "\n".join(res["errors"])
        self.assertIn("rationale is required", msgs)
        self.assertIn("url not in the corpus", msgs)
        self.assertIn("not a GOV.UK URL", msgs)
        self.assertEqual(res["rows"], [])

    def test_rejects_duplicates(self):
        rows = self._sheet(p0=("in", "a"))
        rows.append({"url": "https://gov.uk/p0/", "label": "out", "rationale": "dupe"})
        res = gold.validate_sheet(self.conn, CID, gold.read_sheet(self._write(rows)))
        self.assertTrue(any("duplicate" in e for e in res["errors"]))

    def test_import_upserts_strata_and_status(self):
        rows = self._sheet(p0=("in", "core IHT"), p1=("out", "unrelated"), p3=("borderline", "hmm"))
        res = gold.import_sheet(self.conn, CID, self._write(rows), "tom@example.org")
        self.assertEqual(res["imported"], 3)
        self.assertEqual(sorted(res["unlabelled"]), ["https://www.gov.uk/p2", "https://www.gov.uk/s1"])
        got = {r["url"]: r for r in gold.load_gold(self.conn, CID)}
        p0 = got["https://www.gov.uk/p0"]
        self.assertEqual((p0["label"], p0["rationale"], p0["labelled_by"]),
                         ("in", "core IHT", "tom@example.org"))
        self.assertEqual(p0["content_hash_at_label"], "hash-p0")
        self.assertEqual(p0["content_id"], "cid-p0")
        self.assertEqual(p0["stratum_source"], "both")
        self.assertEqual(p0["stratum_score_band"], "unscored")
        self.assertEqual(p0["seed_origin"], "should_include")
        self.assertEqual(got["https://www.gov.uk/p3"]["seed_origin"], "should_exclude")
        self.assertEqual(got["https://www.gov.uk/p1"]["seed_origin"], "should_include")
        self.assertEqual(p0["gold_version"], 1)
        s = gold.status(self.conn, CID)
        self.assertEqual((s["forwarded"], s["labelled"]), (5, 3))
        self.assertEqual(s["counts"], {"in": 1, "out": 1, "borderline": 1})
        self.assertEqual(len(s["unlabelled"]), 2)
        self.assertEqual(s["drifted"], [])
        self.assertTrue(s["gold_sha"])
        # Re-export pre-fills the labels so work is never lost.
        again = {r["url"]: r for r in gold.build_sheet(self.conn, CID)}
        self.assertEqual(again["https://www.gov.uk/p0"]["label"], "in")
        self.assertEqual(again["https://www.gov.uk/p0"]["rationale"], "core IHT")

    def test_reimport_bumps_version_and_detects_drift(self):
        rows = self._sheet(p0=("in", "core"))
        gold.import_sheet(self.conn, CID, self._write(rows), "a@x")
        sha1 = gold.gold_sha(gold.load_gold(self.conn, CID))
        # The page body changes after labelling…
        self.conn.execute("UPDATE content SET content_hash='hash-p0-v2' WHERE url='https://www.gov.uk/p0'")
        self.conn.commit()
        self.assertEqual(gold.status(self.conn, CID)["drifted"], ["https://www.gov.uk/p0"])
        # …and re-importing the old sheet warns but still upserts (version bumped).
        rows2 = self._sheet(p0=("out", "changed my mind"))
        rows2 = [dict(r, content_hash="hash-p0") if r["url"].endswith("/p0") else r for r in rows2]
        res = gold.import_sheet(self.conn, CID, self._write(rows2), "a@x")
        self.assertTrue(any("drift" in w for w in res["warnings"]))
        p0 = gold.load_gold(self.conn, CID)[0]
        self.assertEqual((p0["label"], p0["gold_version"]), ("out", 2))
        self.assertNotEqual(sha1, gold.gold_sha(gold.load_gold(self.conn, CID)))

    def test_replace_drops_stale_labels(self):
        gold.import_sheet(self.conn, CID, self._write(self._sheet(p0=("in", "a"), p1=("out", "b"))), "a@x")
        # A fresh sheet that only labels p2 (the re-export pre-fills p0/p1, so clear them).
        rows = self._sheet(p2=("in", "c"))
        for r in rows:
            if not r["url"].endswith("/p2"):
                r["label"], r["rationale"] = "", ""
        gold.import_sheet(self.conn, CID, self._write(rows), "a@x", replace=True)
        self.assertEqual([r["url"] for r in gold.load_gold(self.conn, CID)], ["https://www.gov.uk/p2"])


class TestCli(GoldBase):
    def test_export_import_status_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            dbp = os.path.join(d, "t.db")
            # Copy the in-memory fixture into a file DB for the CLI.
            file_conn = db.connect(dbp)
            self.conn.backup(file_conn)
            file_conn.close()
            os.environ["CORPUS_DB"] = dbp
            try:
                sheet = os.path.join(d, "sheet.csv")
                self.assertEqual(gold.main(["export", "--category", str(CID), "--out", sheet]), 0)
                rows = gold.read_sheet(sheet)
                self.assertEqual(len(rows), 5)
                rows[0]["label"], rows[0]["rationale"] = "in", "yes"
                gold.write_sheet(rows, sheet)
                self.assertEqual(gold.main(["import", "--category", str(CID), "--csv", sheet,
                                            "--labelled-by", "t@x"]), 0)
                self.assertEqual(gold.main(["status", "--category", str(CID)]), 0)
                rows[1]["label"] = "nope"
                gold.write_sheet(rows, sheet)
                self.assertEqual(gold.main(["import", "--category", str(CID), "--csv", sheet]), 1)
            finally:
                os.environ.pop("CORPUS_DB", None)


if __name__ == "__main__":
    unittest.main()
