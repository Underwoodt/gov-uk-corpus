# govuk_corpus — corpus rebuild (clean pilot package)

Clean, instrumented rebuild of the GOV.UK corpus. See the intent doc and build plan
in the vault: `projects/gov-uk-corpus/corpus-intent.md` and `build-plan-phase-a.md`.

This package is the **Phase A foundation**. It ports the proven logic from the older
`jobs/` + `scripts/` scripts into a run/provenance/hashing framework, targeting SQLite
now and Postgres on migration.

## Modules
| Module | Role |
|---|---|
| `canonical.py` | URL canonicalisation gate — used on every insert and join. Rejects malformed URLs, promotes `gov.uk` → `www.gov.uk`. |
| `hashing.py` | `content_hash` of raw `/api/content` JSON for change detection. |
| `config.py` | API base, rate limit, DEFRA scope, pilot seed frontier. |
| `schema.sql` | Pilot schema (SQLite, Postgres-ready): `runs`, `fetch_log`, `content`, `sitemap`, `page_organisations`, `page_links`, `redirects`. |
| `db.py` | SQLite access + run/provenance helpers. |
| `extract.py` | Deterministic field + organisation extraction from `/api/content`. |
| `stage_sitemap.py` | **Stage 0** — sitemap frontier refresh (offline dir or live), `lastmod` tracking. |
| `stage_align.py` | **Stage 1** — fetch, hash, upsert with provenance; reads the frontier. |
| `stage_redirects.py` | **Stage 2** — resolve redirect chains to final (depth-capped, cycle-safe), import destination. |
| `stage_attachments.py` | **Stage 3** — expand child/attachment pages into the one corpus; record `page_links`. |
| `backend.py` | Selects the DB backend by env: Postgres (`DB_HOST`/`DB_BACKEND=postgres`) else SQLite. |
| `db_pg.py` | Postgres backend (psycopg3) — mirrors `db.py`'s API so stages run unchanged. |
| `schema_pg.sql` | Postgres schema (parity with `schema.sql`). |
| `run_all.py` | Runs the full cycle (Stage 0→1→2→3) — the daily cron entry point. |
| `shortlist.py` | Emit a deterministic URL shortlist (org × document_type × keyword) for inference. |
| `reconcile.py` | Re-verify held pages (stalest first) to self-heal drift — re-fetch/hash/update, preserving `source`. |
| `metrics.py` | Pure dashboard queries (corpus totals, source breakdown, recent runs) — backend-agnostic, unit-tested. |
| `categories.py` | Saved shortlist specs ("categories") — CRUD + validation; filter fields execute via `shortlist.py`. |
| `pilot.py` | Runnable pilot: seed / existing-db / `--from-frontier`. |

Streamlit app (repo root): `dashboard.py` (ops home) + `pages/1_Shortlist_Builder.py`
(CRUD for saved query specs + live filter execution); shared auth/connection in `ui_common.py`.

## Run the pilot
```bash
# built-in DEFRA seed frontier (a handful of live pages, rate-limited)
python3 -m govuk_corpus.pilot --db data/pilot.db

# run again → reports `unchanged` (content hash matched) = change detection working

# or seed from the existing corpus (read-only)
python3 -m govuk_corpus.pilot --from-content-db ~/Downloads/content.db --limit 15
```

## Tests
```bash
python3 -m unittest -v tests.test_canonical
```

## Run Stage 0 (sitemap frontier)
```bash
# offline: parse the sub-sitemaps already in ./sitemaps/
python3 -m govuk_corpus.stage_sitemap --db data/pilot.db --from-dir sitemaps
# live: fetch a couple of sub-sitemaps from gov.uk
python3 -m govuk_corpus.stage_sitemap --db data/pilot.db --live --limit-sitemaps 2
# then align pages from the frontier
python3 -m govuk_corpus.pilot --db data/pilot.db --from-frontier 50
# resolve redirects to their final destination and import it
python3 -m govuk_corpus.stage_redirects --db data/pilot.db
# expand child/attachment pages into the one corpus
python3 -m govuk_corpus.stage_attachments --db data/pilot.db
```

## Shortlists (the output that feeds inference)
```bash
# URLs about slurry OR nitrate, published by the Environment Agency, as guidance
python3 -m govuk_corpus.shortlist --db data/pilot.db \
  --organisation environment-agency --document-type guidance \
  --keywords slurry,nitrate --match any
# just the count
python3 -m govuk_corpus.shortlist --db data/pilot.db --keywords slurry --count
```

## Ops dashboard (Streamlit)
```bash
pip install streamlit
# local (SQLite pilot)
DASHBOARD_DB=data/pilot.db streamlit run dashboard.py
# server (Postgres via env), password-protected, public port
set -a; . ~/gov-uk-corpus.env; set +a
DASHBOARD_PASSWORD=yourpw streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
```
Shows corpus size, source breakdown (sitemap/redirect/attachment), and the last 7 runs
with status / elapsed / success-rate. Same app will host the search/shortlist UI later.

## Status / next
- **Done:** canonicalisation, schema + provenance (`runs`/`fetch_log`), hashing,
  **Stage 0** (sitemap frontier + `lastmod`), **Stage 1** align + frontier wiring,
  **Stage 2** (redirect chains → final → import), **Stage 3** (child/attachment page
  expansion + `page_links`; file binaries counted, deferred). **28 unit tests pass.**
  All four stages verified end-to-end on the pilot DB.
- **Shortlist output** (`shortlist.py`) — deterministic URL lists by org × document_type ×
  keyword, backend-agnostic; keyword is a substring over `content` for now (FTS index
  deferred). This is the corpus's hand-off to inference.
- **Postgres-ready:** `db_pg.py` + `backend.py` + `schema_pg.sql` run the same stages on
  Postgres by setting `DB_HOST` (needs `pip install "psycopg[binary]"`). `run_all.py` is the
  daily entry point. Migration: `projects/gov-uk-corpus/lightsail-migration-runbook.md`.
- **Phase A code complete** (39 tests). **Next (ops):** provision Lightsail + migrate
  (`pgloader`), schedule `run_all` daily. Later: swap the keyword substring for a real index.
