"""Guarantee every run records its prompt criteria fields, and backfill old runs.

Each run stamps a ``prompt_spec`` JSON snapshot (evaluate.prompt_spec_json) that already
carries the four editable criteria fields — ``inclusion_context``, ``exclusion_context``,
``keep_hints``, ``drop_hints`` (plus ``name``) — so *future* runs are fully covered. Runs
created before that snapshot existed (or before all four keys were added) have a NULL or
partial ``prompt_spec``, so their criteria can't be told apart on the Run performance page.

This module fills the **missing** keys on those older runs from the run's category's
**current** Filter Parameters. Because Filter Parameters are not versioned, a backfilled
value is the value *now*, not necessarily the value the run actually used — so every
backfilled run is stamped ``criteria_backfilled: true`` (with ``criteria_backfilled_keys``
and ``criteria_backfilled_at``) so nothing downstream mistakes a reconstructed value for a
genuinely stamped one.

Idempotent and cheap on steady state: candidates are pre-filtered in SQL to rows whose
``prompt_spec`` is NULL or lacks one of the four key *names*, and a genuinely stamped key is
never overwritten (only absent keys are added). ``db.init_db`` calls ``backfill(conn)`` on
every startup, so the fix ships automatically the next time this version is pulled and the
service restarts on the live server. Also runnable standalone:

    set -a; . ~/gov-uk-corpus.env; set +a
    python3 -m govuk_corpus.backfill_prompt_fields
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

# prompt_spec key -> the categories column it is snapshotted from. `name` is derived
# (see _category_name), so it is handled separately.
_FIELD_COLUMN = {
    "inclusion_context": "inclusion_context",
    "exclusion_context": "exclusion_context",
    "keep_hints": "adjudication_hints_keep",
    "drop_hints": "adjudication_hints_drop",
}
_FIELDS = tuple(_FIELD_COLUMN)


def _category_name(cat, c: Dict) -> str:
    """The run's topic label, matching evaluate.prompt_spec_json's `name`."""
    return (c.get("description") or "").strip() or cat.prettify(c.get("slug")) or "the topic"


def _candidate_sql() -> str:
    # NULL spec, or a spec text missing any of the four key names. When prompt_spec IS NULL
    # the LIKE terms are NULL (unknown); the explicit IS NULL covers that row.
    missing_any = " OR ".join(f"prompt_spec NOT LIKE {_P}" for _ in _FIELDS)
    return (f"SELECT run_id, category_id, prompt_spec FROM evaluation_runs "
            f"WHERE prompt_spec IS NULL OR {missing_any}")


def backfill(conn) -> Dict[str, int]:
    """Fill absent criteria keys on runs that predate full prompt_spec stamping.

    Returns counters: scanned, backfilled, no_category, already_complete.
    """
    from . import categories as cat  # local: avoid an import cycle from db.init_db

    params = tuple(f'%"{k}"%' for k in _FIELDS)
    rows = conn.execute(_candidate_sql(), params).fetchall()

    counters = {"scanned": len(rows), "backfilled": 0, "no_category": 0, "already_complete": 0}
    cat_cache: Dict[int, Dict] = {}

    for row in rows:
        run_id, cid, raw = row["run_id"], row["category_id"], row["prompt_spec"]
        try:
            spec = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            spec = {}
        if not isinstance(spec, dict):
            spec = {}

        # Only ever ADD absent keys — never overwrite a genuinely stamped value (even "").
        missing = [k for k in _FIELDS if k not in spec]
        need_name = "name" not in spec
        if not missing and not need_name:
            counters["already_complete"] += 1
            continue

        if cid not in cat_cache:
            cat_cache[cid] = cat.get_category(conn, cid) or {}
        c = cat_cache[cid]
        if not c:
            counters["no_category"] += 1  # category gone — can't reconstruct; leave as-is
            continue

        for k in missing:
            spec[k] = c.get(_FIELD_COLUMN[k]) or ""
        if need_name:
            spec["name"] = _category_name(cat, c)

        # Provenance: these are the values NOW, not necessarily what the run used.
        spec["criteria_backfilled"] = True
        spec["criteria_backfilled_from"] = "category_current"
        spec["criteria_backfilled_at"] = db.now_iso()
        prev = spec.get("criteria_backfilled_keys") or []
        added = missing + (["name"] if need_name else [])
        spec["criteria_backfilled_keys"] = sorted(set(prev) | set(added))

        conn.execute(f"UPDATE evaluation_runs SET prompt_spec={_P} WHERE run_id={_P}",
                     (json.dumps(spec, ensure_ascii=False), run_id))
        counters["backfilled"] += 1

    if counters["backfilled"]:
        conn.commit()
    return counters


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill missing prompt criteria fields on old runs from current category values.")
    ap.add_argument("--db", default="data/pilot.db", help="SQLite path (ignored for Postgres)")
    args = ap.parse_args()

    if args.db and not _IS_PG:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)  # also runs this backfill; explicit call below reports the residual
    counters = backfill(conn)
    print("prompt-fields backfill complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:18} {v}")
    conn.close()


if __name__ == "__main__":
    main()
