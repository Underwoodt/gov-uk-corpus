# CLAUDE.md — repo conventions

Project conventions that must be followed for every change. This file is the home for
"always do it this way" rules; add new ones here.

## Web pages

- **Every rendered page carries a unique page ID** shown bottom-left in mid-grey,
  formatted `guc-NNNN`. IDs are unique, and **retired but never reused**.
- When you **add a page**, give it the next free serial and register it. The rule, the
  wiring, the *Next free serial*, and the full registry live in
  [PAGE_INDEX.md](PAGE_INDEX.md) — follow its "Adding a page" checklist and keep the
  table up to date. When you **remove a page**, mark its ID Retired there (don't delete
  the row, don't reuse the number).
- Pages are Jinja templates under `webapp/templates/` that `extends "base.html"`; the
  ID is a `{% block page_id %}` set right after the `extends` line.

## App structure

- Web app: FastAPI + Jinja in [webapp/app.py](webapp/app.py); domain logic in the
  `govuk_corpus/` package. Data layer is backend-agnostic — SQLite for local dev and
  tests, Postgres in production — selected by [govuk_corpus/backend.py](govuk_corpus/backend.py).
  Use the active `db` placeholder (`_P`) for SQL; never hardcode `?`/`%s`.
- See [README.md](README.md) for the overview and [RUNBOOK.md](RUNBOOK.md) for setup.

## Tests

- `python -m unittest discover -s tests` — runs on SQLite; Postgres-only tests skip
  unless `DB_BACKEND=postgres` and `DB_*` are set. Add tests with new behaviour.
