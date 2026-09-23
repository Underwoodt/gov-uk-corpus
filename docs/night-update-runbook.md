# Nightly corpus update — runbook

_What the nightly corpus cycle does, in what order, for what benefit; the component behind each
phase; and what each phase reports and where._

The nightly update keeps the local GOV.UK **corpus** (the `content` table and its derived tables)
in step with GOV.UK. Everything the app does downstream — the deterministic shortlist, the funnel
counts, the GOV.UK-coverage augmentation, the AI evaluation — reads from this corpus, so the
freshness and completeness of the whole system depends on this one cycle running cleanly.

- **Entry point:** `python3 -m govuk_corpus.run_all` → [`govuk_corpus/run_all.py`](govuk_corpus/run_all.py) (`run_cycle`).
- **Backend:** chosen by environment ([`govuk_corpus/backend.py`](govuk_corpus/backend.py)) — Postgres when `DB_HOST`/`DB_BACKEND` is set (production), otherwise SQLite (local/dev). On the server, source `/home/ubuntu/gov-uk-corpus.env` first or it silently falls back to a local SQLite dev DB.
- **Scope:** `--scope whole-govuk` (default). The DEFRA work runs against the whole of GOV.UK.

---

## The cycle at a glance

Four fetch stages run in strict order (each stage feeds the next), followed by a non-fetch
tidy-up. Each of the four stages is recorded as its own **run** row.

| # | Phase | Component | What it does | Why it runs here |
|---|-------|-----------|--------------|------------------|
| 0 | Sitemap (frontier) | [`stage_sitemap.py`](govuk_corpus/stage_sitemap.py) `refresh_live` / `refresh_from_dir` | Reads GOV.UK's sitemap index and every sub-sitemap; upserts each `<loc>` into `sitemap` with its `lastmod`, marking new / updated / unchanged. | Cheap discovery of *what exists and what changed*, so later stages fetch only what they must. |
| 1 | Align | [`stage_align.py`](govuk_corpus/stage_align.py) `align_urls` | For the frontier URLs (new / changed `lastmod`), fetches `/api/content`, hashes the JSON, and upserts into `content` with change detection (new / changed / unchanged). Extracts fields, organisations and search text. | Pulls the *actual page bodies + metadata* into the one corpus. This is the core content refresh. |
| 2 | Redirects | [`stage_redirects.py`](govuk_corpus/stage_redirects.py) `run_stage2` | Finds pages GOV.UK marks as `redirect`, follows the chain (depth-capped, cycle-detected) to the final target, records `source → final` in `redirects`, and imports the final page (reusing Stage 1 align). | Keeps the corpus pointed at *live canonical pages*, not dead or aliased URLs. |
| 3 | Attachments | [`stage_attachments.py`](govuk_corpus/stage_attachments.py) `run_stage3` | For pages already in `content`, reads child/attachment references (`links.children`, `details.parts`, inline HTML attachments), records `parent → child` in `page_links`, and imports child pages not already held. | Catches *child pages the sitemap omits* (guidance sub-pages, HTML attachments), so the corpus is complete. Binaries (PDF/spreadsheets) are counted, not fetched. |
| — | Tidy-up | [`run_all.py`](govuk_corpus/run_all.py) `run_tidy_up` | Recomputes derived, cached figures now the corpus is fresh: per-category input-shortlist counts + membership, and per-organisation page counts. | So the app reads *stored* numbers instead of running expensive live counts on every page load. |

Order matters: 0 discovers → 1 fetches the changed set → 2 repairs redirects into that set → 3
expands into linked children → tidy-up recomputes everything derived from the result.

---

## What each phase reports, and where

Every stage is bracketed by `db.start_run(stage=…)` and `db.finish_run(…)`
([`db_pg.py`](govuk_corpus/db_pg.py)), so it leaves a durable record **regardless of the console**:

- **`runs` table** — one row per stage: `run_id`, `stage`, `scope`, `status` (running → complete/failed), `started_at`, `finished_at`, and the phase's **counters as a JSON blob**. This is the authoritative report. Note the same table also carries rows from other maintenance jobs (e.g. `search_text`, `govuk-augment`), so filter by `stage` when you only want the nightly cycle.
- **`fetch_log` table** — the per-URL audit trail behind the counters: `url`, `action` (new / changed / unchanged / redirect / error / invalid / no_content_item), `http_status`, `bytes`, `duration_ms`, `error`.
- **stdout** — a one-line summary per stage via `_print` (`[Stage N …] run abcd1234: k=v, …`). When run under `nohup` this lands in a log file (e.g. `/home/ubuntu/corpus-run.log`); the tidy-up lines print here only (they are not recorded as runs).

### Counters emitted per phase

| Phase | Counters (JSON in `runs.counters`) |
|-------|-------------------------------------|
| 0 Sitemap | `sub_sitemaps`, `urls_seen`, `new`, `updated`, `unchanged`, `invalid` |
| 1 Align | `seen`, `invalid`, `duplicate`, `new`, `changed`, `unchanged`, `no_content_item`, `error` |
| 2 Redirects | `sources`, `resolved`, `gone`, `circular`, `max_depth`, `external`, `error`, `imported_new`, `imported_unchanged`, `imported_changed` |
| 3 Attachments | `parents`, `child_links`, `binaries`, `children_to_import`, `imported_new`, `imported_unchanged`, `imported_no_content_item`, `imported_error` |
| Tidy-up | stdout only: `category counts: <updated>/<total> refreshed`, `organisation page counts: <n> organisations refreshed` |

---

## Where the results are consumed (the benefit, downstream)

The nightly cycle is upstream of almost everything the app shows:

- **`runs.finished_at` (latest)** → `_corpus_version` in the web app, which invalidates the funnel
  count cache, and drives the "Newest snapshot · N pages" banner. It is `MAX(finished_at)` across
  *all* runs, so any completed run (this cycle, or a maintenance job) advances it.
- **`content`** → every deterministic shortlist, funnel count, and the GOV.UK-coverage comparison.
- **`redirects` / `page_links`** → redirect resolution and parent/child expansion.
- **Tidy-up outputs:**
  - `category_page_counts` → the "input shortlist" size shown per row on the Categories list.
  - `category_shortlist_pages` → the persisted deterministic shortlist membership (org + doc-type + keyword).
  - `organisation_page_counts` → organisation page counts used in facets/breakdowns.

---

## Scheduling

`run_all` is written to be "the single entry point for the daily cron job", **but on the current
server there is no cron entry or systemd timer installed** — recent runs have been launched
manually (e.g. `nohup … > corpus-run.log`). To make it truly nightly, install one of:

**systemd timer (recommended)** — `/etc/systemd/system/corpus-cycle.service` +
`corpus-cycle.timer`:

```ini
# corpus-cycle.service
[Service]
Type=oneshot
WorkingDirectory=/home/ubuntu/gov-uk-corpus
EnvironmentFile=/home/ubuntu/gov-uk-corpus.env
ExecStart=/home/ubuntu/gov-uk-corpus/.venv/bin/python -m govuk_corpus.run_all
```

```ini
# corpus-cycle.timer
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
[Install]
WantedBy=timers.target
```

**cron alternative:**

```bash
30 2 * * * cd /home/ubuntu/gov-uk-corpus && set -a && . ./gov-uk-corpus.env && set +a && .venv/bin/python -m govuk_corpus.run_all >> /home/ubuntu/corpus-run.log 2>&1
```

---

## Operational notes

- **Idempotent by design.** Change detection (sitemap `lastmod`, then content hash) means a
  re-run mostly reports `unchanged`; only genuinely new/changed pages are re-fetched.
- **Rate-limited fetching.** Stages 1–3 fetch `/api/content` through a shared `_RateLimiter`
  ([`stage_align.py`](govuk_corpus/stage_align.py)) so the cycle is polite to GOV.UK.
- **Backend gotcha.** For a manual run against production Postgres you MUST load the env first
  (`set -a; . /home/ubuntu/gov-uk-corpus.env; set +a`) — otherwise it connects to an empty local
  SQLite dev DB and appears to do nothing.
- **Failure isolation in tidy-up.** `category_counts.refresh_all` commits per category (one bad
  category can't lose the rest); `orgs.refresh_page_counts` is wrapped so its failure is reported
  and skipped, not fatal.

## Known issues (observed)

- **`pilot.py` is a different, older entry point** (seed/pilot fetch, not the Stage 0–3 cycle). The
  last manual `corpus-run.log` shows `pilot.py` failing with a `content_pkey` UniqueViolation on an
  already-present URL. For the whole-GOV.UK nightly use **`run_all`**, not `pilot`.
- **No full cycle has run recently.** As observed, the newest `runs` rows are `govuk-augment` and
  `search_text`; the last `align` row is from **2026-09-13 and is stuck `status=running`** (never
  finished). A stuck `running` row is normal to see after an interrupted run but should be tidied,
  and it means the corpus has not had a fresh Stage 0–3 pass in a while — the main reason to install
  the schedule above.
- **Funnel-count cache can lag.** The funnel caches its stage counts keyed by a filter hash +
  `_corpus_version`; under some corpus-change sequences a cached count can fall behind the live
  corpus. The funnel's keyword count is now computed **live** to sidestep that class of bug — see
  [ai-selection-funnel.md](docs/ai-selection-funnel.md).

## Verify a run

```bash
# last stages recorded, newest first
psql "$DATABASE_URL" -c "SELECT stage, status, started_at, finished_at, counters FROM runs ORDER BY started_at DESC LIMIT 8;"
```

Healthy cycle: four rows (sitemap, align, redirect, attachment) all `status=complete` with a recent
`finished_at`, followed by the tidy-up lines in the console/log.

---

## Related, but NOT part of this cycle

These use the corpus but are **not** part of `run_all` — they run on their own triggers and each
writes its own `runs` rows / logs:

- **AI evaluation** background runner (two-phase inclusion → exclusion, daily spend cap) — triggered
  per shortlist from the app. Progress logs look like
  `phase=Phase 1 - Inclusion done_this_call=25 remaining=… cum_cost=$… spent_today=$…`
  (e.g. `/home/ubuntu/fresh_run.log`).
- **GOV.UK coverage augmentation** (`stage=govuk-augment`) — the hybrid GOV.UK Search step that
  tags pages Both / Shortlister-only / GOV.UK-only; runs on shortlist save / GOV.UK fetch.
- **Search-text builder** ([`build_search_text.py`](govuk_corpus/build_search_text.py),
  `stage=search_text`) — rebuilds `content.search_text` (HTML-stripped, org-name-stripped) used by
  the keyword filter; a resumable maintenance pass, run with `--rescan` when the stripping logic
  changes.

Don't conflate these with the nightly cycle when reading logs or the `runs` table.
