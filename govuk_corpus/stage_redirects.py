"""Stage 2 — resolve redirects to their FINAL destination and import it.

GOV.UK marks a redirect page with document_type/schema_name == "redirect" and a
`redirects` list: [{"destination": "/some/path", "path": ..., "type": "exact"}].
We follow the chain (depth-capped, cycle-detected) to the final target, record
source -> final in `redirects` (Q4: source->final only), and import the final
page into the one corpus (source="redirect") by reusing Stage 1 align.

The chain resolver takes a `fetch_fn` so it is unit-testable without the network.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import httpx

from . import config
from .backend import db
from .canonical import canonicalise, path_of
from .stage_align import _RateLimiter, align_urls

# fetch_fn(url) -> (http_status, payload_json_or_None)
FetchFn = Callable[[str], Tuple[Optional[int], Optional[dict]]]


def _is_redirect_payload(payload: dict) -> bool:
    return (payload.get("document_type") == "redirect"
            or payload.get("schema_name") == "redirect")


def _next_hop(payload: dict) -> Optional[str]:
    reds = payload.get("redirects") or []
    if not reds:
        return None
    dest = reds[0].get("destination")
    if not dest:
        return None
    if dest.startswith("/"):
        dest = "https://www.gov.uk" + dest
    return canonicalise(dest)


def resolve_chain(fetch_fn: FetchFn, start_url: str,
                  max_depth: int = config.MAX_REDIRECT_DEPTH) -> Dict:
    """Follow a redirect chain. Returns dict(final_url, status, hops, error)."""
    visited: List[str] = []
    current = start_url
    for _ in range(max_depth + 1):
        if current in visited:
            return {"final_url": None, "status": None, "hops": visited, "error": "circular"}
        visited.append(current)
        status, payload = fetch_fn(current)
        if status in (404, 410):
            return {"final_url": current, "status": status, "hops": visited,
                    "error": "gone" if status == 410 else "not_found"}
        if status != 200 or payload is None:
            return {"final_url": None, "status": status, "hops": visited, "error": f"http_{status}"}
        if _is_redirect_payload(payload):
            nxt = _next_hop(payload)
            if nxt is None:
                return {"final_url": None, "status": 200, "hops": visited,
                        "error": "external_or_bad_destination"}
            current = nxt
            continue
        return {"final_url": current, "status": 200, "hops": visited, "error": None}
    return {"final_url": None, "status": None, "hops": visited, "error": "max_depth"}


def _new_counters() -> Dict[str, int]:
    return {k: 0 for k in (
        "sources", "resolved", "circular", "max_depth", "gone", "external", "error",
    )}


def run_stage2(conn, run_id: str, limit: Optional[int] = None) -> Dict[str, int]:
    counters = _new_counters()
    sources = db.find_unresolved_redirects(conn, limit=limit)
    limiter = _RateLimiter(config.RATE_LIMIT_PER_SEC)
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    finals_to_import: List[str] = []

    with httpx.Client(timeout=config.REQUEST_TIMEOUT, follow_redirects=True,
                      headers=headers) as client:
        def fetch_fn(url: str) -> Tuple[Optional[int], Optional[dict]]:
            limiter.wait()
            try:
                resp = client.get(config.GOVUK_API_BASE + (path_of(url) or ""))
            except httpx.HTTPError:
                return None, None
            if resp.status_code != 200:
                return resp.status_code, None
            return 200, resp.json()

        for source in sources:
            counters["sources"] += 1
            result = resolve_chain(fetch_fn, source)
            final = result["final_url"]
            err = result["error"]
            db.upsert_redirect(conn, source, final, result["status"], run_id)
            db.log_fetch(conn, run_id, "redirect", source,
                         "redirect" if err is None else (err or "error"),
                         http_status=result["status"],
                         error=None if err is None else f"{err}: {' -> '.join(result['hops'])}")
            if err is None and final:
                finals_to_import.append(final)
            elif err in ("gone", "not_found"):
                counters["gone"] += 1
            elif err == "circular":
                counters["circular"] += 1
            elif err == "max_depth":
                counters["max_depth"] += 1
            elif err == "external_or_bad_destination":
                counters["external"] += 1
            else:
                counters["error"] += 1
            conn.commit()

    counters["resolved"] = len(finals_to_import)
    # Import the final destinations into the one corpus (reuse Stage 1 align).
    if finals_to_import:
        import_counters = align_urls(conn, run_id, finals_to_import,
                                     source="redirect", stage="redirect")
        counters["imported_new"] = import_counters["new"]
        counters["imported_unchanged"] = import_counters["unchanged"]
        counters["imported_changed"] = import_counters["changed"]
    return counters


def main() -> None:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="Stage 2 — resolve redirects to final + import.")
    ap.add_argument("--db", default="data/pilot.db")
    ap.add_argument("--limit", type=int, help="cap number of redirects to resolve")
    ap.add_argument("--scope", default="defra-pilot")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)
    run_id = db.start_run(conn, stage="redirect", scope=args.scope)
    counters = run_stage2(conn, run_id, limit=args.limit)
    db.finish_run(conn, run_id, counters)

    print(f"Stage 2 run {run_id[:8]} complete. Counters:")
    for k, v in counters.items():
        print(f"  {k:20} {v}")
    total = conn.execute("SELECT COUNT(*) AS n FROM redirects").fetchone()["n"]
    print(f"\nredirects table: {total} source->final mappings")
    conn.close()


if __name__ == "__main__":
    main()
