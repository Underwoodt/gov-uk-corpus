# GOV.UK Corpus — Content Shortlist Builder

A FastAPI + Jinja web app, styled in the GOV.UK / Defra Design System, for building
**content shortlists** from a corpus of ~877k gov.uk pages. You define a *category*
(organisations + document types + keywords + inclusion/exclusion context), the app
narrows the corpus with a deterministic **selection funnel**, then optionally runs
**LLM inclusion/exclusion passes** to produce a reviewable, exportable shortlist.

- **Live pilot:** `http://18.171.159.148:8600`
- **Repo:** `AI-Accelerator-Defra/gov-uk-corpus`
- **Full schema reference:** [database.md](database.md)

---

## What it does

```
                       ┌──────────────────────── Category (saved spec) ────────────────────────┐
                       │  organisations · document types · keywords · inclusion/exclusion ctx   │
                       └───────────────────────────────────────────────────────────────────────┘
                                                     │
   corpus (content)  ──▶  Selection funnel (deterministic, SQL)                    ──▶  Input Shortlist
   ~877k pages            org semi-join → document-type filter → keyword (tsvector/LIKE)
                                                     │
                                                     ▼
                          LLM evaluation (optional, chained)
                          Inclusion pass  →  Exclusion pass  →  Final shortlist
                          (sync or batch; runs in the foreground or as a
                           server-side background job)
                                                     │
                                                     ▼
                          Review + export  (CSV · XLSX · JSON, custom field selector)
```

### Key features

- **Categories** — create/edit saved shortlist specs, or start from a worked example
  (e.g. the "slurry" preset) or the guided **category interview** assistant.
- **Selection funnel** — a live, cached Sankey + table showing how each filter narrows
  the corpus (organisation → document type → keyword → input shortlist, then the LLM
  inclusion/exclusion branches with dropped counts).
- **Keyword Matching / Semantic Match / Funnel Results** tabs on the category page,
  with organisation and keyword breakdowns.
- **AI evaluation** — inclusion then exclusion passes, chained via `source_run_id`.
  Runs **synchronously** or in **batch** mode (per-phase selector on Settings), and
  can run **server-side in the background** so you can leave the page. Multiple
  categories can evaluate concurrently; one background run per category.
- **Run performance & detail** — per-run keep/drop totals, "pages evaluated / target"
  context, run commentary, *Continue LLM evaluation* / *Complete this run* actions,
  and identification of any unparsed page.
- **Audit Shortlist tab** — inspect and export the shortlist at any pipeline stage:
  **Department → Document type → Keyword → Included → Final** — with a column picker,
  title search, paging, and a stage-aware download page.
- **Export** — CSV, XLSX, or JSON with a customisable field selector; the download page
  defaults to the fields on screen but every field is selectable.
- **Settings** — AI model catalogue (add/test models), daily AI spend budget and guard,
  sync/batch mode per phase, peak-hours schedule, and user-role configuration.

---

## Architecture

| Layer | What | Where |
|-------|------|-------|
| Web app | FastAPI + Jinja2, GOV.UK-styled | [webapp/app.py](webapp/app.py), `webapp/templates/`, `webapp/static/` |
| Domain logic | Categories, shortlist queries, funnel, evaluation, settings, roles | `govuk_corpus/` (e.g. [shortlist.py](govuk_corpus/shortlist.py), [evaluate.py](govuk_corpus/evaluate.py), [categories.py](govuk_corpus/categories.py)) |
| Data layer | **Backend-agnostic** — SQLite locally, Postgres in production | [govuk_corpus/backend.py](govuk_corpus/backend.py) selects [db.py](govuk_corpus/db.py) (SQLite) or [db_pg.py](govuk_corpus/db_pg.py) (psycopg 3) |
| Corpus build | Crawl/ingest stages that populate the `content` corpus | `govuk_corpus/stage_*.py`, [pilot.py](govuk_corpus/pilot.py) |
| Schema | SQLite + Postgres DDL, applied on startup (`IF NOT EXISTS`) | [schema.sql](govuk_corpus/schema.sql), [schema_pg.sql](govuk_corpus/schema_pg.sql), [schema_auth.sql](govuk_corpus/schema_auth.sql) |

**Backend selection** is by environment (see [backend.py](govuk_corpus/backend.py)):

- Postgres when `DB_BACKEND=postgres` **or** `DB_HOST` is set → `db_pg` (psycopg 3, `%s` placeholders).
- SQLite otherwise (the default; used by the pilot and the test suite) → `db`.

`psycopg` is only imported when Postgres is selected, so local SQLite runs and the
tests need no Postgres driver installed.

---

## Quick start (local, SQLite)

```bash
cd "/Users/tomunderwood/AI Brain/gov-uk-corpus"

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build a small local SQLite corpus (see RUNBOOK.md for options)
python -m govuk_corpus.pilot --db data/pilot.db --from-content-db ~/Downloads/content.db --limit 200

# Run the app against that SQLite DB
CORPUS_DB=data/pilot.db DASHBOARD_PASSWORD=devpass \
  uvicorn webapp.app:app --reload --port 8600
```

Open <http://localhost:8600> and sign in with the `DASHBOARD_PASSWORD` you set.

> Full step-by-step for both local and production (Postgres on Lightsail/EC2 with
> systemd) lives in **[RUNBOOK.md](RUNBOOK.md)**.

---

## Configuration

Set via environment (production values live in `~/gov-uk-corpus.env`, `chmod 600`,
loaded by the systemd unit — never commit real secrets):

| Variable | Purpose | Default |
|----------|---------|---------|
| `CORPUS_DB` | SQLite path (local dev) | `data/pilot.db` |
| `DB_BACKEND` | `postgres` to force the Postgres backend | *(unset → SQLite)* |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Postgres connection (setting `DB_HOST` also selects Postgres) | — |
| `DASHBOARD_PASSWORD` | Shared password for the login gate (HMAC `sb_auth` cookie) | — |
| `ANTHROPIC_API_KEY` / provider keys | LLM evaluation (server only, in the env file) | — |
| `COUNT_CACHE_TTL` | Funnel/count cache TTL (seconds) | `300` |
| `DB_STATEMENT_TIMEOUT_MS` | Postgres statement timeout | `15000` |
| `AI_TIMEOUT` | Per-call LLM timeout (seconds) | `45` |

> **Auth note:** access is currently a single shared `DASHBOARD_PASSWORD`. Real user
> accounts, sessions, and per-user RBAC are being built on the `accounts` branch
> (see the plan in `.claude/plans/`); they are not on `main` yet.

---

## Running the tests

```bash
python -m unittest discover -s tests
# ~194 tests, runs on SQLite. Postgres-only (auth) tests skip unless
# DB_BACKEND=postgres and DB_* are set.
```

---

## Deploying

Production runs under **systemd** as `uvicorn webapp.app:app` on port **8600**,
with `DB_*` + `DASHBOARD_PASSWORD` + API keys sourced from `~/gov-uk-corpus.env`.
See [deploy/corpus-shortlist.service](deploy/corpus-shortlist.service) and the
**Production** section of [RUNBOOK.md](RUNBOOK.md).

---

## Project layout

```
gov-uk-corpus/
├── webapp/
│   ├── app.py               # FastAPI app: routes, funnel, evaluation, export
│   ├── templates/           # Jinja2 (GOV.UK-styled) — preview.html, download.html, settings.html, …
│   └── static/              # govuk.css and assets
├── govuk_corpus/            # domain package
│   ├── backend.py           # SQLite/Postgres selector
│   ├── db.py / db_pg.py     # data layer (SQLite / psycopg 3)
│   ├── categories.py        # saved shortlist specs
│   ├── shortlist.py         # funnel query builder + export
│   ├── evaluate.py          # LLM inclusion/exclusion passes, run chaining
│   ├── settings.py, roles.py, ai_models.py, pricing.py, …
│   ├── stage_*.py, pilot.py # corpus ingest / crawl stages
│   └── schema*.sql          # SQLite / Postgres / auth DDL
├── deploy/corpus-shortlist.service   # systemd unit
├── tests/                   # unittest suite (SQLite)
├── database.md              # full schema & data-model reference
├── RUNBOOK.md               # step-by-step setup (local + production)
├── requirements.txt
└── README.md                # this file
```

> **Legacy:** the original crawler/search-API prototype (`crawlers/`, `api/`,
> `database/schema.sql`, `dashboard.py`) predates the Shortlist Builder and is kept
> for reference only. The active application is `webapp/` + `govuk_corpus/`.

---

**Status:** pilot in use · **Corpus:** ~877k gov.uk pages · **Stack:** FastAPI · Jinja2 · psycopg 3 · Postgres (prod) / SQLite (dev & tests) · Anthropic + configurable LLM providers
