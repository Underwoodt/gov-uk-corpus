"""Stage 1 — align pages: fetch /api/content, hash, upsert with provenance.

Change detection: fetch, hash the raw JSON, compare to the stored hash ->
new | unchanged | changed. (In the full pipeline the sitemap `lastmod` is the
cheap prefilter that decides *whether* to fetch; the pilot fetches its small seed
set every run so the hash comparison is exercised directly.)
"""
from __future__ import annotations

import time
from typing import Dict, Iterable, List

import httpx

from . import config
from .backend import db
from .canonical import canonicalise, path_of
from .extract import extract_fields, extract_organisations
from .hashing import content_hash


def _new_counters() -> Dict[str, int]:
    return {k: 0 for k in (
        "seen", "invalid", "duplicate", "new", "changed", "unchanged",
        "no_content_item", "error",
    )}


class _RateLimiter:
    def __init__(self, per_sec: float) -> None:
        self._min_interval = 1.0 / per_sec if per_sec > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last = time.monotonic()


def align_urls(conn, run_id: str, urls: Iterable[str], source: str,
               stage: str = "align", lastmods: Dict[str, str] = None) -> Dict[str, int]:
    """Fetch/hash/upsert each URL. `lastmods` maps canonical URL -> sitemap lastmod,
    stored on the content row so the frontier query can detect future changes."""
    lastmods = lastmods or {}
    counters = _new_counters()
    limiter = _RateLimiter(config.RATE_LIMIT_PER_SEC)
    seen_canonical: set = set()
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}

    with httpx.Client(timeout=config.REQUEST_TIMEOUT, follow_redirects=True,
                      headers=headers) as client:
        for raw in urls:
            counters["seen"] += 1
            url = canonicalise(raw)
            if url is None:
                counters["invalid"] += 1
                db.log_fetch(conn, run_id, stage, str(raw), "invalid",
                             error="failed canonicalisation")
                continue
            if url in seen_canonical:
                counters["duplicate"] += 1
                db.log_fetch(conn, run_id, stage, url, "skipped",
                             error="duplicate in batch")
                continue
            seen_canonical.add(url)

            api_url = config.GOVUK_API_BASE + path_of(url)
            t0 = time.monotonic()
            try:
                limiter.wait()
                resp = client.get(api_url)
            except httpx.HTTPError as exc:
                counters["error"] += 1
                db.log_fetch(conn, run_id, stage, url, "error", error=f"{type(exc).__name__}: {exc}")
                continue
            dur_ms = int((time.monotonic() - t0) * 1000)

            if resp.status_code in (404, 410):
                counters["no_content_item"] += 1
                db.log_fetch(conn, run_id, stage, url, "no_content_item",
                             http_status=resp.status_code, duration_ms=dur_ms)
                continue
            if resp.status_code != 200:
                counters["error"] += 1
                db.log_fetch(conn, run_id, stage, url, "error",
                             http_status=resp.status_code, duration_ms=dur_ms)
                continue

            raw_json = resp.text
            new_hash = content_hash(raw_json)
            prior_hash = db.get_content_hash(conn, url)
            first_time = prior_hash is None
            changed = first_time or (new_hash != prior_hash)
            action = "new" if first_time else ("changed" if changed else "unchanged")

            payload = resp.json()
            fields = extract_fields(payload)
            fields.update({
                "content": raw_json,
                "content_hash": new_hash,
                "source": source,
                "http_status": resp.status_code,
            })
            if url in lastmods:
                fields["sitemap_lastmod"] = lastmods[url]
            db.upsert_content(conn, url, run_id, fields, changed=changed, first_time=first_time)
            if changed:
                db.replace_page_organisations(conn, url, extract_organisations(payload))

            counters[action] += 1
            db.log_fetch(conn, run_id, stage, url, action,
                         http_status=200, bytes_=len(raw_json), duration_ms=dur_ms)
            conn.commit()

    return counters
