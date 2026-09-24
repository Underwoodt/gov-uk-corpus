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


class TestRunLabelling(GoldBase):
    """The in-app path (guc-0029): rate a run's outcome per page -> gold label."""

    def _chain(self):
        p1 = evaluate.create_run(self.conn, CID, "m", "anthropic")
        evaluate.save_page(self.conn, p1, CID, "https://www.gov.uk/p0", {"keep": 1, "score": 0.9, "reason": "core"}, 5)
        evaluate.save_page(self.conn, p1, CID, "https://www.gov.uk/p1", {"keep": 1, "score": 0.4, "reason": "some"}, 5)
        evaluate.save_page(self.conn, p1, CID, "https://www.gov.uk/p2", {"keep": 0, "score": 0.0, "reason": "no"}, 5)
        evaluate.save_page(self.conn, p1, CID, "https://www.gov.uk/p3", None, 5)
        p2 = evaluate.create_run(self.conn, CID, "m", "anthropic", phase=evaluate.PHASE_EXCLUSION, source_run_id=p1)
        evaluate.save_page(self.conn, p2, CID, "https://www.gov.uk/p0", {"keep": 1, "reason": "still"}, 5)
        evaluate.save_page(self.conn, p2, CID, "https://www.gov.uk/p1", {"keep": 0, "reason": "homonym"}, 5)
        return p1, p2

    def test_stage_and_label_mapping(self):
        self.assertEqual(gold.stage_of_result({"keep": 0}, None), "p1_drop")
        self.assertEqual(gold.stage_of_result({"keep": None}, None), "p1_unparsed")
        self.assertEqual(gold.stage_of_result({"keep": 1}, None), "kept")
        self.assertEqual(gold.stage_of_result({"keep": 1}, {"keep": 0}), "p2_drop")
        self.assertEqual(gold.stage_of_result({"keep": 1}, {"keep": 1}), "kept")
        self.assertEqual(gold.stage_of_result({"keep": 1}, {"keep": None}), "p2_unparsed")
        self.assertIsNone(gold.stage_of_result(None, None))
        self.assertEqual(gold.label_from_verdict("kept", "correct"), "in")
        self.assertEqual(gold.label_from_verdict("kept", "wrong"), "out")
        self.assertEqual(gold.label_from_verdict("p1_drop", "correct"), "out")
        self.assertEqual(gold.label_from_verdict("p2_drop", "wrong"), "in")
        self.assertEqual(gold.label_from_verdict("p1_drop", "borderline"), "borderline")
        self.assertIsNone(gold.label_from_verdict("p1_unparsed", "correct"))
        self.assertEqual(gold.verdict_from_label("p2_drop", "in"), "wrong")
        self.assertEqual(gold.verdict_from_label("kept", "in"), "correct")
        self.assertIsNone(gold.verdict_from_label("kept", ""))

    def test_run_pages_and_save_verdict(self):
        p1, _ = self._chain()
        # Keyword hits live on the shortlist membership, keyed by content_id: p0 is stored under
        # its own URL; p1's hits are stored under an alias URL (a multi-URL guide's base), so a
        # plain URL join would miss them and only the content_id resolution finds them.
        self.conn.execute("INSERT INTO category_shortlist_pages (category_id, content_id, url, matched_keywords) "
                          "VALUES (?,?,?,?)", (CID, "cid-p0", "https://www.gov.uk/p0", '["slurry", "manure"]'))
        self.conn.execute("INSERT INTO category_shortlist_pages (category_id, content_id, url, matched_keywords) "
                          "VALUES (?,?,?,?)", (CID, "cid-p1", "https://www.gov.uk/p1-guide", '["slurry"]'))
        pages = {p["url"]: p for p in gold.run_pages(self.conn, self._category(), p1)}
        self.assertEqual(pages["https://www.gov.uk/p0"]["matched_keywords"], ["slurry", "manure"])
        self.assertEqual(pages["https://www.gov.uk/p1"]["matched_keywords"], ["slurry"])
        self.assertEqual(pages["https://www.gov.uk/p2"]["matched_keywords"], [])
        # Raw document type + parent type (effective type is the parent's for html_publication).
        self.conn.execute("UPDATE content SET document_type='html_publication', parent_document_type='guidance' "
                          "WHERE url='https://www.gov.uk/p2'")
        pages2 = {p["url"]: p for p in gold.run_pages(self.conn, self._category(), p1)}
        self.assertEqual(pages2["https://www.gov.uk/p2"]["raw_document_type"], "html_publication")
        self.assertEqual(pages2["https://www.gov.uk/p2"]["parent_document_type"], "guidance")
        self.assertEqual(pages2["https://www.gov.uk/p2"]["document_type"], "guidance")
        self.assertEqual(pages2["https://www.gov.uk/p0"]["raw_document_type"], "guidance")
        self.assertEqual(pages2["https://www.gov.uk/p0"]["parent_document_type"], "")
        self.assertEqual(sorted(pages), ["https://www.gov.uk/p0", "https://www.gov.uk/p1",
                                         "https://www.gov.uk/p2", "https://www.gov.uk/p3"])
        self.assertEqual(pages["https://www.gov.uk/p0"]["stage"], "kept")
        self.assertEqual(pages["https://www.gov.uk/p1"]["stage"], "p2_drop")
        self.assertEqual(pages["https://www.gov.uk/p1"]["p2"]["reason"], "homonym")
        self.assertEqual(pages["https://www.gov.uk/p2"]["stage"], "p1_drop")
        self.assertEqual(pages["https://www.gov.uk/p3"]["stage"], "p1_unparsed")
        self.assertEqual(pages["https://www.gov.uk/p0"]["seed_label"], "should_include")
        self.assertEqual(pages["https://www.gov.uk/p0"]["source"], "both")
        self.assertEqual(pages["https://www.gov.uk/p0"]["label"], "")
        cat_ = self._category()
        self.assertEqual(gold.save_verdict(self.conn, cat_, pages["https://www.gov.uk/p1"], "wrong", "it is IHT", "t@x"), "in")
        self.assertEqual(gold.save_verdict(self.conn, cat_, pages["https://www.gov.uk/p2"], "correct", "unrelated", "t@x"), "out")
        self.assertEqual(gold.save_verdict(self.conn, cat_, pages["https://www.gov.uk/p0"], "borderline", "meh", "t@x"), "borderline")
        with self.assertRaises(ValueError):
            gold.save_verdict(self.conn, cat_, pages["https://www.gov.uk/p3"], "correct", "x", "t@x")   # unparsed
        with self.assertRaises(ValueError):
            gold.save_verdict(self.conn, cat_, pages["https://www.gov.uk/p0"], "correct", "  ", "t@x")  # no reason
        got = {r["url"]: r for r in gold.load_gold(self.conn, CID)}
        self.assertEqual(got["https://www.gov.uk/p1"]["label"], "in")
        self.assertEqual(got["https://www.gov.uk/p1"]["stratum_score_band"], "0.4-0.6")
        self.assertEqual(got["https://www.gov.uk/p1"]["seed_origin"], "should_include")
        self.assertEqual(got["https://www.gov.uk/p1"]["content_hash_at_label"], "hash-p1")
        self.assertEqual(got["https://www.gov.uk/p1"]["labelled_by"], "t@x")
        # Re-reading the run shows the verdict against this run's outcome.
        pages = {p["url"]: p for p in gold.run_pages(self.conn, cat_, p1)}
        self.assertEqual(pages["https://www.gov.uk/p1"]["verdict"], "wrong")
        self.assertEqual(pages["https://www.gov.uk/p2"]["verdict"], "correct")
        # Clearing removes the label; the exported sheet then carries the rest.
        self.assertIsNone(gold.save_verdict(self.conn, cat_, pages["https://www.gov.uk/p0"], "", "", "t@x"))
        self.assertEqual(sorted(r["url"] for r in gold.load_gold(self.conn, CID)),
                         ["https://www.gov.uk/p1", "https://www.gov.uk/p2"])
        self.assertEqual(gold.status(self.conn, CID)["counts"], {"in": 1, "out": 1, "borderline": 0})


try:
    import fastapi  # noqa: F401
    _HAS_WEBAPP = True
except Exception:
    _HAS_WEBAPP = False


@unittest.skipUnless(_HAS_WEBAPP, "fastapi not installed")
class TestGoldRoutes(unittest.TestCase):
    """guc-0029: the page renders a run's pages with stage badges; the API writes gold labels."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["CORPUS_DB"] = self.path
        os.environ.pop("DB_HOST", None)
        os.environ["DASHBOARD_PASSWORD"] = ""
        os.environ["AUTH_MODE"] = "shared"
        import importlib
        import webapp.app as app
        importlib.reload(app)
        self.app = app
        conn = app.connect()
        app.db.init_db(conn)
        for slug, body in (("p0", "slurry"), ("p1", "slurry"), ("p2", "slurry")):
            url = f"https://www.gov.uk/{slug}"
            conn.execute("INSERT INTO content (url, document_type, is_redirect, content_hash, search_text, title, content_id) "
                         "VALUES (?, 'guidance',0,?,?,?,?)", (url, f"h-{slug}", body, f"Title {slug}", f"c-{slug}"))
            conn.execute("INSERT INTO page_organisations (page_url, organisation_content_id, organisation_slug, role) "
                         "VALUES (?,?,?,?)", (url, "environment-agency", "environment-agency", "primary"))
        from govuk_corpus import categories as cat
        self.cid = cat.create_category(conn, {"slug": "demo", "owner_email": "a@b.co", "description": "Demo",
                                              "dept_slugs": "environment-agency", "document_type_slugs": "guidance",
                                              "keywords": "slurry", "inclusion_context": "slurry"})
        self.p1 = evaluate.create_run(conn, self.cid, "m", "anthropic")
        evaluate.save_page(conn, self.p1, self.cid, "https://www.gov.uk/p0", {"keep": 1, "score": 0.9, "reason": "yes"}, 5)
        evaluate.save_page(conn, self.p1, self.cid, "https://www.gov.uk/p1", {"keep": 0, "score": 0.0, "reason": "no"}, 5)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ.pop("CORPUS_DB", None)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _client(self):
        from fastapi.testclient import TestClient
        return TestClient(self.app.app)

    def test_page_renders_and_api_labels(self):
        c = self._client()
        r = c.get(f"/categories/{self.cid}/gold")
        self.assertEqual(r.status_code, 200)
        self.assertIn("guc-0029", r.text)
        self.assertIn("Kept to the end", r.text)
        self.assertIn("Dropped at Phase 1", r.text)
        self.assertIn("https://www.gov.uk/p1", r.text)
        self.assertIn("Keywords matched", r.text)
        self.assertIn("Document type", r.text)
        tok = c.cookies.get("sb_csrf")
        r = c.post(f"/api/categories/{self.cid}/gold", headers={"X-CSRF-Token": tok or ""},
                   json={"run": self.p1, "url": "https://www.gov.uk/p1", "verdict": "wrong", "rationale": "it is slurry"})
        self.assertEqual(r.status_code, 400)                      # shared mode: a labeller name is required
        r = c.post(f"/api/categories/{self.cid}/gold", headers={"X-CSRF-Token": tok or ""},
                   json={"run": self.p1, "url": "https://www.gov.uk/p1", "verdict": "wrong", "rationale": "it is slurry",
                         "labeller": "ann"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["label"], "in")
        self.assertEqual(r.json()["my_label"], "in")
        self.assertEqual(r.json()["counts"], {"in": 1, "out": 0, "borderline": 0})
        # a second labeller, blind, disagrees -> split -> borderline; votes come back
        r = c.post(f"/api/categories/{self.cid}/gold", headers={"X-CSRF-Token": tok or ""},
                   json={"run": self.p1, "url": "https://www.gov.uk/p1", "label": "out", "rationale": "no",
                         "labeller": "bob", "blind": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["label"], r.json()["agreement"]), ("borderline", "split"))
        self.assertEqual([(v["labeller"], v["label"], v["blind"]) for v in r.json()["votes"]],
                         [("ann", "in", False), ("bob", "out", True)])
        r = c.get(f"/categories/{self.cid}/gold/agreement")
        self.assertEqual(r.status_code, 200)
        self.assertIn("guc-0030", r.text)
        self.assertIn("ann vs bob", r.text)
        r = c.post(f"/api/categories/{self.cid}/gold/adjudicate", headers={"X-CSRF-Token": tok or ""},
                   json={"url": "https://www.gov.uk/p1", "label": "in", "note": "agreed after discussion", "adjudicator": "ann"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["label"], r.json()["agreement"]), ("in", "adjudicated"))
        r = c.post(f"/api/categories/{self.cid}/gold", headers={"X-CSRF-Token": tok or ""},
                   json={"run": self.p1, "url": "https://www.gov.uk/p0", "verdict": "correct", "rationale": ""})
        self.assertEqual(r.status_code, 400)
        r = c.post(f"/api/categories/{self.cid}/gold", headers={"X-CSRF-Token": tok or ""},
                   json={"run": self.p1, "url": "https://www.gov.uk/p2", "verdict": "correct", "rationale": "x"})
        self.assertEqual(r.status_code, 404)                      # not in this run
        r = c.get(f"/categories/{self.cid}/gold/sheet.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("order_hint,url", r.text)
        self.assertIn("it is slurry", r.text)                     # the label is pre-filled in the sheet
        r = c.get(f"/categories/{self.cid}/gold")
        self.assertIn("labellers so far: ann, bob", r.text)
        self.assertIn('id="blind"', r.text)


class TestSampling(unittest.TestCase):
    def _pages(self):
        return ([{"url": f"https://www.gov.uk/d{i}", "stage": "p1_drop", "label": ""} for i in range(60)]
                + [{"url": f"https://www.gov.uk/k{i}", "stage": "kept", "label": ""} for i in range(10)]
                + [{"url": "https://www.gov.uk/u", "stage": "p1_unparsed", "label": ""}])

    def test_defaults_full_for_small_stages(self):
        self.assertEqual(gold.default_targets({"p1_drop": 60, "kept": 10, "p2_drop": 0}), {"p1_drop": 40, "kept": 10})

    def test_selection_is_seeded_incremental_and_uniform(self):
        pages = self._pages()
        a = gold.sample_selection(pages, {"p1_drop": 20, "kept": 10})
        b = gold.sample_selection(list(reversed(pages)), {"p1_drop": 20, "kept": 10})
        self.assertEqual(a["p1_drop"]["urls"], b["p1_drop"]["urls"])          # order of input irrelevant
        self.assertEqual((a["p1_drop"]["n"], a["p1_drop"]["target"]), (60, 20))
        self.assertAlmostEqual(a["p1_drop"]["frac"], 1 / 3)
        self.assertEqual(a["kept"]["frac"], 1.0)
        bigger = gold.sample_selection(pages, {"p1_drop": 30})
        self.assertTrue(a["p1_drop"]["urls"] <= bigger["p1_drop"]["urls"])     # raising only adds
        self.assertNotEqual(a["p1_drop"]["urls"], gold.sample_selection(pages, {"p1_drop": 20}, seed=7)["p1_drop"]["urls"])
        self.assertEqual(gold.sample_selection(pages, {"p1_drop": 999})["p1_drop"]["target"], 60)   # clamped
        self.assertEqual(a["p1_unparsed"]["target"], 1)                          # no target -> whole stage
        gold.apply_sampling(pages, a)
        self.assertEqual(sum(1 for p in pages if p["sampled"]), 31)
        d = next(p for p in pages if p["sampled"] and p["stage"] == "p1_drop")
        self.assertAlmostEqual(d["sample_frac"], 1 / 3)
        pages[0]["label"] = "out"
        prog = gold.sample_progress(pages, a)
        self.assertEqual(prog["p1_drop"]["target"], 20)
        self.assertEqual(prog["p1_drop"]["labelled_total"], 1)

    def test_weight_of(self):
        self.assertEqual(gold.weight_of({"sample_frac": 0.25}), 4.0)
        self.assertEqual(gold.weight_of({"sample_frac": None}), 1.0)
        self.assertEqual(gold.weight_of({}), 1.0)


class TestSampledVerdict(GoldBase):
    def test_save_verdict_stamps_sampling(self):
        p1 = evaluate.create_run(self.conn, CID, "m", "anthropic")
        for slug, keep in (("p0", 0), ("p1", 0), ("p2", 0), ("p3", 1)):
            evaluate.save_page(self.conn, p1, CID, f"https://www.gov.uk/{slug}",
                               {"keep": keep, "score": 0.8 if keep else 0.0, "reason": "r"}, 5)
        cat_ = self._category()
        pages = gold.run_pages(self.conn, cat_, p1)
        sel = gold.sample_selection(pages, {"p1_drop": 2, "kept": 1})
        gold.apply_sampling(pages, sel)
        sampled_drop = next(p for p in pages if p["sampled"] and p["stage"] == "p1_drop")
        unsampled_drop = next(p for p in pages if not p["sampled"] and p["stage"] == "p1_drop")
        gold.save_verdict(self.conn, cat_, sampled_drop, "wrong", "should be in", "t@x", sample_run_id=p1)
        gold.save_verdict(self.conn, cat_, unsampled_drop, "correct", "fine", "t@x", sample_run_id=p1)
        got = {r["url"]: r for r in gold.load_gold(self.conn, CID)}
        s = got[sampled_drop["url"]]
        self.assertEqual((s["sample_run_id"], s["sample_stage"]), (p1, "p1_drop"))
        self.assertAlmostEqual(s["sample_frac"], 2 / 3)
        self.assertEqual(gold.weight_of(s), 1.5)
        u = got[unsampled_drop["url"]]
        self.assertIsNone(u["sample_frac"])
        self.assertEqual(gold.weight_of(u), 1.0)


class TestMultiLabeller(GoldBase):
    def _run(self):
        p1 = evaluate.create_run(self.conn, CID, "m", "anthropic")
        for slug, keep in (("p0", 1), ("p1", 0), ("p2", 0), ("p3", 1)):
            evaluate.save_page(self.conn, p1, CID, f"https://www.gov.uk/{slug}",
                               {"keep": keep, "score": 0.8 if keep else 0.0, "reason": "r"}, 5)
        return p1

    def test_consensus_rules(self):
        v = lambda who, lbl: {"labeller": who, "label": lbl, "rationale": "r"}
        self.assertIsNone(gold.consensus([]))
        self.assertEqual(gold.consensus([v("a", "in")])["agreement"], "single")
        c = gold.consensus([v("a", "in"), v("b", "in")])
        self.assertEqual((c["label"], c["agreement"], c["n_votes"]), ("in", "unanimous", 2))
        c = gold.consensus([v("a", "in"), v("b", "out")])
        self.assertEqual((c["label"], c["agreement"]), ("borderline", "split"))
        c = gold.consensus([v("a", "in"), v("b", "out"), v("c", "in")])
        self.assertEqual((c["label"], c["agreement"]), ("in", "majority"))
        c = gold.consensus([v("a", "in"), v("b", "out"), v("c", "borderline")])
        self.assertEqual((c["label"], c["agreement"]), ("borderline", "split"))
        self.assertIn("a: in", gold.consensus([v("a", "in"), v("b", "out")])["rationale"])

    def test_votes_consensus_adjudication_and_agreement(self):
        p1 = self._run()
        cat_ = self._category()
        pages = {p["url"]: p for p in gold.run_pages(self.conn, cat_, p1)}
        u0, u1 = "https://www.gov.uk/p0", "https://www.gov.uk/p1"
        # Labeller A: p0 kept & correct -> in; p1 dropped & wrong -> in
        self.assertEqual(gold.save_verdict(self.conn, cat_, pages[u0], "correct", "core", "a@x", sample_run_id=p1), "in")
        self.assertEqual(gold.save_verdict(self.conn, cat_, pages[u1], "wrong", "belongs", "a@x", sample_run_id=p1), "in")
        # Labeller B agrees on p0, disagrees on p1 (blind, direct label)
        self.assertEqual(gold.save_verdict(self.conn, cat_, pages[u0], "correct", "yes", "b@x"), "in")
        self.assertEqual(gold.save_verdict(self.conn, cat_, pages[u1], "", "not really", "b@x", label="out", blind=True),
                         "borderline")                                   # 2-way split -> borderline
        gl = {r["url"]: r for r in gold.load_gold(self.conn, CID)}
        self.assertEqual((gl[u0]["label"], gl[u0]["agreement"], gl[u0]["n_votes"]), ("in", "unanimous", 2))
        self.assertEqual((gl[u1]["label"], gl[u1]["agreement"]), ("borderline", "split"))
        self.assertEqual(gl[u0]["labelled_by"], "a@x, b@x")
        votes = {(v["url"], v["labeller"]): v for v in gold.load_votes(self.conn, CID)}
        self.assertEqual(votes[(u1, "b@x")]["blind"], 1)
        self.assertEqual(votes[(u1, "a@x")]["blind"], 0)
        self.assertEqual(gold.labellers(self.conn, CID), ["a@x", "b@x"])
        # run_pages exposes the votes per page and the consensus
        pages = {p["url"]: p for p in gold.run_pages(self.conn, cat_, p1)}
        self.assertEqual(pages[u1]["agreement"], "split")
        self.assertEqual({v["labeller"]: v["label"] for v in pages[u1]["votes"]}, {"a@x": "in", "b@x": "out"})
        # agreement stats
        ag = gold.agreement_stats(self.conn, CID)
        self.assertEqual((ag["pages_multi"], ag["unanimous"], len(ag["disagreements"])), (2, 1, 1))
        self.assertEqual(ag["disagreements"][0]["url"], u1)
        self.assertEqual(ag["pairs"][0]["shared"], 2)
        self.assertAlmostEqual(ag["pairs"][0]["agree"], 0.5)
        self.assertIsNotNone(ag["fleiss_kappa"])
        self.assertEqual(ag["blind_votes"], 1)
        # adjudicate the split: label + note; votes untouched
        self.assertEqual(gold.adjudicate(self.conn, CID, u1, "in", "discussed: it is IHT guidance", "a@x"), "in")
        gl = {r["url"]: r for r in gold.load_gold(self.conn, CID)}
        self.assertEqual((gl[u1]["label"], gl[u1]["agreement"], gl[u1]["adjudicated_by"]), ("in", "adjudicated", "a@x"))
        self.assertEqual(len(gold.load_votes(self.conn, CID, u1)), 2)
        # a new vote does not override an adjudication
        gold.save_verdict(self.conn, cat_, pages[u1], "", "meh", "c@x", label="out")
        self.assertEqual(gold.load_gold(self.conn, CID)[1]["label"] if False else {r["url"]: r for r in gold.load_gold(self.conn, CID)}[u1]["label"], "in")
        # clearing the adjudication recomputes: a in, b out, c out -> majority out
        self.assertEqual(gold.adjudicate(self.conn, CID, u1, "", "", "a@x"), "out")
        with self.assertRaises(ValueError):
            gold.adjudicate(self.conn, CID, u1, "in", "  ", "a@x")
        with self.assertRaises(ValueError):
            gold.save_verdict(self.conn, cat_, pages[u0], "correct", "x", "")      # no labeller
        # removing all votes removes the consensus row
        for who in ("a@x", "b@x", "c@x"):
            gold.save_verdict(self.conn, cat_, pages[u1], "", "", who)
        self.assertNotIn(u1, {r["url"] for r in gold.load_gold(self.conn, CID)})
        st = gold.status(self.conn, CID)
        self.assertEqual(st["agreement"]["labellers"], ["a@x", "b@x"])

    def test_sheet_import_is_a_vote_per_labeller(self):
        rows = gold.build_sheet(self.conn, CID)
        for r in rows:
            if r["url"].endswith("/p0"):
                r["label"], r["rationale"] = "in", "sheet says in"
        d = tempfile.mkdtemp(); path = os.path.join(d, "a.csv"); gold.write_sheet(rows, path)
        gold.import_sheet(self.conn, CID, path, "a@x")
        for r in rows:
            if r["url"].endswith("/p0"):
                r["label"], r["rationale"] = "out", "sheet b says out"
        gold.write_sheet(rows, path)
        gold.import_sheet(self.conn, CID, path, "b@x")
        gl = {r["url"]: r for r in gold.load_gold(self.conn, CID)}
        self.assertEqual((gl["https://www.gov.uk/p0"]["label"], gl["https://www.gov.uk/p0"]["agreement"]), ("borderline", "split"))
        self.assertEqual(len(gold.load_votes(self.conn, CID)), 2)
        # --replace only resets that labeller's votes
        gold.import_sheet(self.conn, CID, path, "b@x", replace=True)
        self.assertEqual(len(gold.load_votes(self.conn, CID)), 2)

    def test_backfill_migrates_legacy_consensus_rows_to_votes(self):
        self.conn.execute("INSERT INTO category_gold_labels (category_id, url, label, rationale, labelled_by) "
                          "VALUES (?,?,?,?,?)", (CID, "https://www.gov.uk/p0", "in", "old", "tom@x"))
        self.conn.commit()
        db.init_db(self.conn)          # idempotent migrations incl. the backfill
        db.init_db(self.conn)
        votes = gold.load_votes(self.conn, CID)
        self.assertEqual([(v["labeller"], v["label"]) for v in votes], [("tom@x", "in")])
