"""Gold labels for the pipeline quality benchmark (see docs/bench/protocol.md).

A gold label is a human's verdict on one page for one category: `in` (the page belongs in
the category's final shortlist), `out` (it does not) or `borderline` (a reasonable person
could go either way), plus a one-line rationale. Labels live in `category_gold_labels`
and are the ground truth the benchmark (`govuk_corpus.bench`) scores the AI pipeline against.

Workflow (one labeller, a spreadsheet):

    python3 -m govuk_corpus.gold export --category CID --out docs/bench/gold/<slug>-sheet.csv
    ... fill the `label` and `rationale` columns ...
    python3 -m govuk_corpus.gold import --category CID --csv <sheet> --labelled-by <email>
    python3 -m govuk_corpus.gold status --category CID

`export` writes exactly the pages a fresh Phase-1 run would evaluate for the category (the
"forwarded set": the keyword shortlist plus GOV.UK-Search-only pages above the relevance
floor), enriched with the latest run's verdicts so the labeller can see what the AI thought,
in a **stratified order** (score band x source x document type, round-robin) so that
labelling the first N rows already covers every stratum. Rows already labelled are
pre-filled, so a re-export never loses work. `import` validates the whole sheet before
writing anything. Pages whose body changed since labelling are flagged as drifted.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import categories as cat
from . import evaluate, orgs, shortlist
from .backend import db
from .canonical import canonicalise

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

LABELS = ("in", "out", "borderline")
SEED_INCLUDE = "should_include"
SEED_EXCLUDE = "should_exclude"

# A run id no real run will ever have: run_candidates excludes pages already evaluated
# *in this run*, so a never-used id yields the whole forwarded set.
EXPORT_RUN_ID = "gold-export"
DEFAULT_MIN_ES_SCORE = float(os.getenv("GOVUK_MIN_ES_SCORE", "0.005"))
DEFAULT_SEED = 42
DESCRIPTION_LIMIT = 300

SHEET_COLUMNS = [
    "order_hint", "url", "title", "description", "document_type", "source", "es_score",
    "current_score", "confidence", "p1_keep", "p2_keep", "p1_reason", "primary_topic",
    "seed_label", "content_hash", "govuk_link", "label", "rationale",
]
STRATA_FIELDS = ("stratum_score_band", "stratum_source", "stratum_doc_type")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- the forwarded set ------------------------------------------------------

def effective_filters(conn, category: Dict) -> dict:
    """The filters a run applies for this category (mirrors webapp.app._effective_filters
    without importing the app): org/doc-type/keyword lists, orgs expanded to descendants
    when the category says so."""
    f = dict(
        organisations=cat.parse_list(category.get("dept_slugs")),
        document_types=cat.parse_list(category.get("document_type_slugs")),
        keywords=cat.parse_list(category.get("keywords")),
        match="any",
    )
    if category.get("include_child_orgs") and f["organisations"]:
        f["organisations"] = orgs.expand_with_children(conn, f["organisations"], recursive=True)
    return f


def _search_page_index(conn, category_id: int) -> Dict[str, dict]:
    rows = conn.execute(
        f"SELECT url, source, es_score FROM category_search_pages WHERE category_id = {_P}",
        (category_id,)).fetchall()
    return {r["url"]: dict(r) for r in rows}


def _content_meta(conn, urls: Sequence[str]) -> Dict[str, dict]:
    """content_id, effective document type and current content_hash per URL."""
    out: Dict[str, dict] = {}
    urls = list(urls)
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        marks = ",".join([_P] * len(chunk))
        rows = conn.execute(
            f"SELECT c.url AS url, c.content_id AS content_id, c.content_hash AS content_hash, "
            f"{shortlist._EFF_DOCTYPE_EXPR} AS document_type "
            f"FROM content c WHERE c.url IN ({marks})", tuple(chunk)).fetchall()
        for r in rows:
            out[r["url"]] = dict(r)
    return out


def forwarded_pages(conn, category: Dict, *, min_es_score: float = DEFAULT_MIN_ES_SCORE,
                    include_below_floor: bool = False) -> List[dict]:
    """The pages a fresh Phase-1 run would evaluate for the category, each tagged with its
    source: `both` (keyword shortlist + GOV.UK Search), `shortlister` (keyword shortlist
    only), `search` (GOV.UK-Search-only, at/above the floor) or `search_below_floor`
    (only with `include_below_floor`)."""
    cid = int(category["id"])
    flt = effective_filters(conn, category)
    rows = evaluate.run_candidates(conn, EXPORT_RUN_ID, cid, 1_000_000,
                                   min_es_score=min_es_score, **flt)
    sp = _search_page_index(conn, cid)
    got = {r["url"] for r in rows}
    if include_below_floor:
        below = conn.execute(
            f"SELECT c.url AS url, c.title AS title, c.description AS description, "
            f"c.content_hash AS content_hash "
            f"FROM category_search_pages sp JOIN content c ON c.url = sp.url "
            f"WHERE sp.category_id = {_P} AND sp.source = 'search' "
            f"AND c.is_redirect = 0 AND c.content_hash IS NOT NULL "
            f"ORDER BY c.url", (cid,)).fetchall()
        for r in below:
            if r["url"] not in got:
                d = dict(r)
                d["_below_floor"] = True
                rows.append(d)
                got.add(d["url"])
    meta = _content_meta(conn, [r["url"] for r in rows])
    out = []
    for r in rows:
        s = sp.get(r["url"])
        if r.get("_below_floor"):
            source = "search_below_floor"
        elif s and s.get("source") == "search":
            source = "search"
        elif s and s.get("source") == "both":
            source = "both"
        else:
            source = "shortlister"
        m = meta.get(r["url"], {})
        out.append({
            "url": r["url"],
            "title": r.get("title") or "",
            "description": (r.get("description") or "")[:DESCRIPTION_LIMIT],
            "document_type": m.get("document_type") or "",
            "content_id": m.get("content_id"),
            "content_hash": m.get("content_hash") or r.get("content_hash"),
            "source": source,
            "es_score": s.get("es_score") if s else None,
        })
    return out


# ---- latest AI verdicts (context for the labeller) ---------------------------

def latest_verdicts(conn, category_id: int) -> Dict[str, dict]:
    """Per URL: the latest inclusion run's score/keep/reason/primary_topic and, if it has
    an exclusion run, that run's keep."""
    incl = evaluate.latest_inclusion_run(conn, category_id)
    if not incl:
        return {}
    out: Dict[str, dict] = {}
    for r in evaluate.run_results(conn, incl):
        out[r["url"]] = {"current_score": r.get("score"), "p1_keep": r.get("keep"),
                         "p1_reason": r.get("reason") or "",
                         "primary_topic": r.get("primary_topic") or "", "p2_keep": None}
    excl = evaluate.latest_exclusion_run(conn, incl)
    if excl:
        for r in evaluate.run_results(conn, excl):
            if r["url"] in out:
                out[r["url"]]["p2_keep"] = r.get("keep")
    return out


# ---- seeds from the category's URL checklists --------------------------------

def seed_labels(category: Dict) -> Dict[str, str]:
    """url -> should_include | should_exclude from the category's URL-check lists
    (canonicalised). Never written as a label without the human's verdict."""
    out: Dict[str, str] = {}
    for field, origin in (("should_include_urls", SEED_INCLUDE),
                          ("should_exclude_urls", SEED_EXCLUDE)):
        for raw in cat.parse_list(category.get(field)):
            u = canonicalise(raw.split()[0]) if raw.strip() else None
            if u:
                out[u] = origin
    return out


# ---- strata + ordering -------------------------------------------------------

def score_band(score) -> str:
    """Bands aligned with evaluate.confidence_label's thresholds."""
    if score is None or score == "":
        return "unscored"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unscored"
    if s <= 0:
        return "0"
    if s <= 0.35:
        return "0.1-0.3"
    if s <= 0.65:
        return "0.4-0.6"
    return "0.7-1.0"


def stratum_of(row: Dict) -> Tuple[str, str, str]:
    return (score_band(row.get("current_score")), row.get("source") or "",
            row.get("document_type") or "")


def _shuffle_key(seed: int, url: str) -> str:
    # Per-row seeded key: adding/removing pages never reorders the others.
    return hashlib.sha1(f"{seed}:{url}".encode("utf-8")).hexdigest()


def stratified_order(rows: List[Dict], seed: int = DEFAULT_SEED) -> List[Dict]:
    """Round-robin across strata (smallest stratum first when ties), seeded shuffle within
    each stratum. Deterministic for a given seed; a page's position relative to the others
    in its stratum never changes between exports."""
    groups: Dict[Tuple[str, str, str], List[Dict]] = {}
    for r in rows:
        groups.setdefault(stratum_of(r), []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: _shuffle_key(seed, r["url"]))
    order = sorted(groups.keys(), key=lambda k: (len(groups[k]), k))
    out: List[Dict] = []
    queues = {k: list(groups[k]) for k in order}
    while any(queues.values()):
        for k in order:
            if queues[k]:
                out.append(queues[k].pop(0))
    return out


# ---- gold table access ---------------------------------------------------------

def load_gold(conn, category_id: int, labelled_only: bool = True) -> List[dict]:
    sql = f"SELECT * FROM category_gold_labels WHERE category_id = {_P}"
    if labelled_only:
        sql += " AND label IS NOT NULL"
    sql += " ORDER BY url"
    return [dict(r) for r in conn.execute(sql, (category_id,)).fetchall()]


def gold_sha(rows: Iterable[dict]) -> str:
    """A fingerprint of the labelled set (url + label), so a run can record exactly which
    gold it was scored against."""
    lines = sorted(f"{r['url']}\t{r['label']}" for r in rows)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def upsert_label(conn, category_id: int, row: Dict) -> None:
    cols = ["category_id", "url", "content_id", "label", "rationale", "labelled_by",
            "labelled_at", "content_hash_at_label", "stratum_score_band", "stratum_source",
            "stratum_doc_type", "seed_origin"]
    vals = [category_id, row["url"], row.get("content_id"), row["label"], row.get("rationale"),
            row.get("labelled_by"), row.get("labelled_at") or now_iso(),
            row.get("content_hash_at_label"), row.get("stratum_score_band"),
            row.get("stratum_source"), row.get("stratum_doc_type"), row.get("seed_origin")]
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols[2:])
    conn.execute(
        f"INSERT INTO category_gold_labels ({', '.join(cols)}) "
        f"VALUES ({', '.join([_P] * len(cols))}) "
        f"ON CONFLICT (category_id, url) DO UPDATE SET {sets}, "
        f"gold_version = category_gold_labels.gold_version + 1",
        tuple(vals))


# ---- export --------------------------------------------------------------------

def build_sheet(conn, category_id: int, *, seed: int = DEFAULT_SEED,
                min_es_score: float = DEFAULT_MIN_ES_SCORE,
                include_below_floor: bool = False) -> List[dict]:
    category = cat.get_category(conn, category_id)
    if not category:
        raise SystemExit(f"category {category_id} not found")
    pages = forwarded_pages(conn, category, min_es_score=min_es_score,
                            include_below_floor=include_below_floor)
    verdicts = latest_verdicts(conn, category_id)
    seeds = seed_labels(category)
    existing = {r["url"]: r for r in load_gold(conn, category_id, labelled_only=False)}
    rows: List[dict] = []
    for p in pages:
        v = verdicts.get(p["url"], {})
        g = existing.get(p["url"], {})
        rows.append({
            "url": p["url"], "title": p["title"], "description": p["description"],
            "document_type": p["document_type"], "source": p["source"],
            "es_score": p["es_score"],
            "current_score": v.get("current_score"),
            "confidence": evaluate.confidence_label(v.get("current_score")),
            "p1_keep": v.get("p1_keep"), "p2_keep": v.get("p2_keep"),
            "p1_reason": v.get("p1_reason", ""), "primary_topic": v.get("primary_topic", ""),
            "seed_label": seeds.get(p["url"], ""),
            "content_hash": p["content_hash"] or "",
            "govuk_link": p["url"],
            "label": g.get("label") or "", "rationale": g.get("rationale") or "",
        })
    ordered = stratified_order(rows, seed=seed)
    for i, r in enumerate(ordered, start=1):
        r["order_hint"] = i
    return ordered


def write_sheet(rows: List[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in SHEET_COLUMNS})


# ---- import --------------------------------------------------------------------

def read_sheet(path: str) -> List[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def validate_sheet(conn, category_id: int, sheet: List[dict]) -> dict:
    """Validate every row before anything is written. Returns
    {rows: [ready-to-upsert dicts], errors: [str], warnings: [str], unlabelled: [url]}.
    A single error means nothing is imported."""
    category = cat.get_category(conn, category_id)
    if not category:
        return {"rows": [], "errors": [f"category {category_id} not found"],
                "warnings": [], "unlabelled": []}
    seeds = seed_labels(category)
    errors: List[str] = []
    warnings: List[str] = []
    unlabelled: List[str] = []
    ready: List[dict] = []
    seen: Dict[str, int] = {}
    raw_urls = [(i, (r.get("url") or "").strip()) for i, r in enumerate(sheet, start=2)]
    canon = {raw: canonicalise(raw) for _, raw in raw_urls if raw}
    meta = _content_meta(conn, [u for u in canon.values() if u])
    for line, r in zip((i for i, _ in raw_urls), sheet):
        raw = (r.get("url") or "").strip()
        if not raw:
            errors.append(f"line {line}: empty url")
            continue
        url = canon.get(raw)
        if not url:
            errors.append(f"line {line}: not a GOV.UK URL: {raw}")
            continue
        if url in seen:
            errors.append(f"line {line}: duplicate of line {seen[url]}: {url}")
            continue
        seen[url] = line
        label = (r.get("label") or "").strip().lower()
        rationale = (r.get("rationale") or "").strip()
        if not label:
            unlabelled.append(url)
            continue
        if label not in LABELS:
            errors.append(f"line {line}: label must be one of {', '.join(LABELS)}: {label!r}")
            continue
        if not rationale:
            errors.append(f"line {line}: rationale is required ({url})")
            continue
        m = meta.get(url)
        if not m:
            errors.append(f"line {line}: url not in the corpus: {url}")
            continue
        sheet_hash = (r.get("content_hash") or "").strip()
        if sheet_hash and m.get("content_hash") and sheet_hash != m["content_hash"]:
            warnings.append(f"line {line}: body changed since the sheet was exported "
                            f"(content_hash drift): {url}")
        ready.append({
            "url": url, "label": label, "rationale": rationale,
            "content_id": m.get("content_id"),
            "content_hash_at_label": sheet_hash or m.get("content_hash"),
            "stratum_score_band": score_band(r.get("current_score")),
            "stratum_source": (r.get("source") or "").strip() or None,
            "stratum_doc_type": (r.get("document_type") or "").strip() or m.get("document_type"),
            "seed_origin": seeds.get(url),
        })
    return {"rows": ready, "errors": errors, "warnings": warnings, "unlabelled": unlabelled}


def import_sheet(conn, category_id: int, path: str, labelled_by: Optional[str],
                 replace: bool = False) -> dict:
    """Validate then upsert. Raises ValueError (nothing written) when the sheet has errors."""
    result = validate_sheet(conn, category_id, read_sheet(path))
    if result["errors"]:
        raise ValueError("\n".join(result["errors"]))
    ts = now_iso()
    if replace:
        conn.execute(f"DELETE FROM category_gold_labels WHERE category_id = {_P}", (category_id,))
    for row in result["rows"]:
        row["labelled_by"] = labelled_by
        row["labelled_at"] = ts
        upsert_label(conn, category_id, row)
    conn.commit()
    result["imported"] = len(result["rows"])
    return result


# ---- status --------------------------------------------------------------------

def status(conn, category_id: int, *, min_es_score: float = DEFAULT_MIN_ES_SCORE) -> dict:
    category = cat.get_category(conn, category_id)
    if not category:
        raise SystemExit(f"category {category_id} not found")
    pages = forwarded_pages(conn, category, min_es_score=min_es_score)
    labels = load_gold(conn, category_id)
    by_url = {r["url"]: r for r in labels}
    current = {p["url"]: p for p in pages}
    counts = {k: 0 for k in LABELS}
    per_stratum: Dict[str, Dict[str, int]] = {}
    drifted: List[str] = []
    for r in labels:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
        key = " / ".join(str(r.get(f) or "-") for f in STRATA_FIELDS)
        per_stratum.setdefault(key, {k: 0 for k in LABELS})[r["label"]] += 1
        p = current.get(r["url"])
        if p and r.get("content_hash_at_label") and p["content_hash"] \
                and p["content_hash"] != r["content_hash_at_label"]:
            drifted.append(r["url"])
    unlabelled = [u for u in current if u not in by_url]
    extra = [u for u in by_url if u not in current]  # labelled but no longer forwarded
    return {
        "category_id": category_id, "forwarded": len(pages), "labelled": len(labels),
        "counts": counts, "unlabelled": unlabelled, "not_forwarded": extra,
        "drifted": drifted, "per_stratum": per_stratum,
        "borderline_share": (counts["borderline"] / len(labels)) if labels else 0.0,
        "gold_sha": gold_sha(labels) if labels else None,
    }


# ---- CLI -------------------------------------------------------------------------

def _connect():
    conn = db.connect(os.getenv("CORPUS_DB", "content.db"))
    db.init_db(conn)
    return conn


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m govuk_corpus.gold", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="write the labelling sheet (CSV)")
    ex.add_argument("--category", type=int, required=True)
    ex.add_argument("--out", required=True)
    ex.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ex.add_argument("--min-es-score", type=float, default=DEFAULT_MIN_ES_SCORE)
    ex.add_argument("--include-below-floor", action="store_true",
                    help="also list GOV.UK-Search-only pages below the relevance floor")
    im = sub.add_parser("import", help="validate a labelled sheet and upsert its labels")
    im.add_argument("--category", type=int, required=True)
    im.add_argument("--csv", required=True)
    im.add_argument("--labelled-by", default=os.getenv("GOLD_LABELLED_BY"))
    im.add_argument("--replace", action="store_true",
                    help="delete the category's existing labels first")
    st = sub.add_parser("status", help="labels per stratum, unlabelled, drifted")
    st.add_argument("--category", type=int, required=True)
    st.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    conn = _connect()
    try:
        if a.cmd == "export":
            rows = build_sheet(conn, a.category, seed=a.seed, min_es_score=a.min_es_score,
                               include_below_floor=a.include_below_floor)
            write_sheet(rows, a.out)
            labelled = sum(1 for r in rows if r["label"])
            strata = len({stratum_of(r) for r in rows})
            print(f"wrote {len(rows)} pages ({labelled} already labelled, {strata} strata) "
                  f"to {a.out}")
            return 0
        if a.cmd == "import":
            try:
                res = import_sheet(conn, a.category, a.csv, a.labelled_by, replace=a.replace)
            except ValueError as e:
                print("NOT imported — fix these and re-run:\n" + str(e), file=sys.stderr)
                return 1
            for w in res["warnings"]:
                print("warning:", w, file=sys.stderr)
            print(f"imported {res['imported']} labels; {len(res['unlabelled'])} rows still "
                  f"unlabelled; {len(res['warnings'])} drift warnings")
            return 0
        if a.cmd == "status":
            s = status(conn, a.category)
            if a.json:
                print(json.dumps(s, indent=2))
                return 0
            c = s["counts"]
            print(f"category {s['category_id']}: forwarded {s['forwarded']}, labelled "
                  f"{s['labelled']} (in {c['in']}, out {c['out']}, borderline {c['borderline']}; "
                  f"borderline share {s['borderline_share']:.0%}), unlabelled "
                  f"{len(s['unlabelled'])}, drifted {len(s['drifted'])}, labelled-but-not-"
                  f"forwarded {len(s['not_forwarded'])}, gold_sha {s['gold_sha']}")
            for key in sorted(s["per_stratum"]):
                v = s["per_stratum"][key]
                print(f"  {key}: in {v['in']}  out {v['out']}  borderline {v['borderline']}")
            for u in s["drifted"]:
                print("  drifted:", u)
            return 0
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
