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
- **Sub-tabs carry an identifier too**, so a specific tab can be named in a bug report:
  the page's ID plus a letter (`a`, `b`, `c`, …) — e.g. the Preview page (`guc-0003`)
  sub-tabs are `guc-0003a` / `guc-0003b` / `guc-0003c`. Each sub-tab carries its id as a
  `data-tabid` attribute; the **active** sub-tab's id shows in light grey in the
  **bottom-left corner of the tab pane** via a single `<span class="pane-id">`, updated
  by the sub-tab `show()` handler (not inside the tab label). Set the page ID once as
  `{% set page_ref = "guc-NNNN" %}` and reuse it for the `page_id` block and the
  `data-tabid`s. When you add or remove a sub-tab, keep the letters contiguous and update
  the tab list in [PAGE_INDEX.md](PAGE_INDEX.md).

## Tables / lists

- **Column headers wrap aggressively.** A table/list heading must never widen its
  column just to fit on one line — it wraps onto multiple lines (breaking mid-word if
  needed) so the column can stay as narrow as its data. This is the global default
  (`th { white-space: normal; overflow-wrap: break-word }` in `govuk.css`); don't set
  `white-space: nowrap` on a `th` to force a single line.

## App structure

- Web app: FastAPI + Jinja in [webapp/app.py](webapp/app.py); domain logic in the
  `govuk_corpus/` package. Data layer is backend-agnostic — SQLite for local dev and
  tests, Postgres in production — selected by [govuk_corpus/backend.py](govuk_corpus/backend.py).
  Use the active `db` placeholder (`_P`) for SQL; never hardcode `?`/`%s`.
- See [README.md](README.md) for the overview and [RUNBOOK.md](RUNBOOK.md) for setup.

## Requirements & decisions

- **Feature specs / reference docs** live in `docs/` (e.g. [docs/gds-audit.md](docs/gds-audit.md)).
- **Decisions** are recorded as lightweight ADRs in `docs/decisions/NNNN-*.md`
  (Context / Decision / Consequences). Add one when you make a non-obvious choice; this
  is the durable "why". This file (CLAUDE.md) is for coding conventions, not decisions.

## Tests

- `python -m unittest discover -s tests` — runs on SQLite; Postgres-only tests skip
  unless `DB_BACKEND=postgres` and `DB_*` are set. Add tests with new behaviour.
