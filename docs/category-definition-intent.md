# Category Definition — Intent & Reference

> Status: reflects the system as built (2026-09). This document has two halves:
> the **intent** (what a category definition is *for* and the requirements it meets)
> and a factual **"under the hood"** description of what the system actually does today.
> Where the two might be confused, sections are labelled *(intent)* or *(built)*.

## 1. Purpose *(intent)*

A **category** is a saved, reusable definition of "the gov.uk pages about one topic".
Its job is to turn a broad corpus (~800k+ gov.uk pages) into a **small, focused,
relevant dataset** — cheaply and transparently — *before* any large-language-model
work happens.

The guiding principle is **deterministic first, LLM last**:

1. **Deterministic filtering** narrows the whole corpus to a shortlist using plain,
   inspectable SQL (organisations → document types → keywords). This is fast, free,
   repeatable, and fully explainable — you can see the exact query and counts at every
   step.
2. **The LLM stage** then judges only that shortlist (keep/drop with a reason and a
   score). Because the shortlist is already small and on-topic, the LLM spend is bounded
   and the results are higher quality than running a model over the raw corpus.

A category therefore stores **two kinds of fields**:

- **Filter fields** (executed against the corpus): organisations, document types,
  keywords.
- **Inference fields** (handed to the LLM): an inclusion description, an exclusion
  description, adjudication hints, and optional URL overrides.

The definition is authored once and re-run whenever the corpus refreshes, so results
stay comparable over time.

## 2. Requirements *(intent)*

### Functional
- **R1 — Save a named topic definition.** A category has a stable, human-readable name
  (slug), an owner, and a description. The name is fixed after creation so runs stay
  comparable.
- **R2 — Filter by organisation.** Choose one or more publishing organisations; a page
  qualifies if one of them published it or is named on it. Optionally include child
  organisations (e.g. Defra also pulls in Environment Agency, Natural England, RPA…).
- **R3 — Filter by document type.** Choose the gov.uk document types to keep, with a
  live per-organisation page count so the choice is informed. Types are only selectable
  once at least one organisation is chosen (counts are organisation-scoped).
- **R4 — Filter by keyword.** Provide inclusion terms (one per line or comma-separated).
  A term is one word or a two-word phrase; word endings are matched (stemming); case is
  ignored.
- **R5 — Preview the deterministic result before spending anything.** Show, per stage,
  how many pages survive each filter, which organisations and which terms contributed,
  and the actual pages in the shortlist — with a download.
- **R6 — Describe the topic for the LLM.** Capture an *include* description and an
  *exclude* description (plus optional keep/drop adjudication hints) in plain English.
- **R7 — Run the LLM pipeline over the shortlist**, in capped batches, with per-run cost
  and keep/drop counts, and the ability to compare models.
- **R8 — Optional URL overrides.** Pin specific guidance/legislation URLs into the set,
  or restrict the set to only those URLs.
- **R9 — Transparency.** Advanced users can see the exact SQL (parameters inlined)
  behind every list and funnel stage.

### Non-functional
- **N1 — Cheap by default.** The deterministic funnel must run without any API key and
  without per-row model calls; counts are cached until the definition or corpus changes.
- **N2 — Explainable.** Every number on the preview traces to a query the user can read.
- **N3 — Backend-agnostic.** Domain logic runs on SQLite (local dev/tests) and Postgres
  (production) via a single placeholder abstraction; production keyword search uses the
  Postgres full-text index.
- **N4 — Reproducible.** Re-running a definition against the same corpus snapshot yields
  the same shortlist; the fixed name keeps historical runs comparable.
- **N5 — Guided authoring.** A non-expert can build a definition either by filling the
  form or by being interviewed by an assistant.

## 3. How to create a category definition *(how-to)*

The flow is **define → preview the deterministic filtering → hand a focused dataset to
the LLM**. Two entry points build the same definition.

### 3a. Author the definition
- From **Categories**, choose **Create a new category** — this opens the **definition
  form** (`guc-0002`). New categories also offer a **"Complete with the assistant"**
  banner that opens a chat interview (`guc-0013`); the assistant fills the same form and
  hands its answers back for review.
- Fill in, in order:
  1. **Your email** (run notifications) and a **Name** (lowercase slug, fixed after
     creation).
  2. **Organisations** — search the list and tick the publishers to include. Leave none
     ticked to search all. Optionally tick **Include child organisations**.
  3. **Page Types** — locked until an organisation is chosen. Once unlocked it lists the
     document types those organisations publish, each with its page count
     (e.g. `Guidance (8,829)`), largest first. Tick the ones to keep, or none for all.
  4. **Inclusion keywords** — one term per line or comma-separated (a term is ≤ 2 words).
  5. **Include / Exclude descriptions** and optional **keep/drop hints** — the plain-
     English brief for the LLM stage.
  6. Optional **guidance / legislation URL overrides**.
- Save. The category is created as a **draft**.

### 3b. Preview the deterministic filtering *(before any LLM spend)*
Open the category's **Preview and run** page (`guc-0003`). Nothing here calls a model.

- **Selection funnel** — a timed table + Sankey showing the corpus narrowing one stage
  at a time: **All pages → after organisations → after document types → after keywords**,
  with the count kept and rejected at each stage.
- **Keyword Matching** tab:
  - **Which organisations matched** — pages contributed per organisation.
  - **Which terms matched** — pages matching each keyword individually.
  - **Pages in this shortlist** — the actual deterministic result set (title, document
    type, URL), paginated, with a **Download…** (CSV/JSON/XLSX).
  - **Show SQL** (advanced users) — the exact query behind each list/stage, parameters
    inlined.
- **Audit Results** (`guc-0004`) — browse/extract the pages at any funnel stage with
  selectable columns and export.

This is the checkpoint: confirm the shortlist is small and on-topic. Tune
organisations / document types / keywords until it is. Only then proceed.

### 3c. Hand the focused dataset to the LLM
On the **AI Pipeline** tab, run the evaluation over the shortlist (not the corpus):
- **Execute Active Run** evaluates up to a configured number of pages per run, in the
  background, resuming where it left off.
- Each run is a fixed model, so you can start another run with a different model and
  compare keep/drop and cost.
- **Funnel Results** shows the per-page keep/drop decisions, confidence and reason, with
  a download.

## 4. Process built *(built)*

```
        AUTHOR                     DETERMINISTIC FUNNEL (SQL, cached, free)                 LLM PIPELINE (per-run, costed)
  ┌───────────────┐        ┌───────────────────────────────────────────────┐      ┌───────────────────────────────────┐
  │ Form (guc-0002)│  →     │ All pages                                      │      │ Phase 1 — Inclusion  (keep/drop)  │
  │  or Assistant  │        │  → after organisations                         │  →   │ Phase 2 — Exclusion  (re-check)   │
  │  (guc-0013)    │        │  → after document types (effective type)       │      │ Phase 3 — Adjudication (hints)    │
  └───────────────┘        │  → after keywords (full-text, stemmed)          │      └───────────────────────────────────┘
                           │  = SHORTLIST  (preview guc-0003, audit guc-0004)│
                           └───────────────────────────────────────────────┘
```

1. **Author** the definition (form or assistant) → stored in `categories`.
2. **Funnel** narrows the corpus with one COUNT per stage; results cached in
   `category_page_counts` (refreshed on save and by the nightly tidy-up).
3. **Shortlist** is the output of the keyword stage — previewed and downloadable.
4. **LLM runs** evaluate only the shortlist, tracked per run in `evaluation_runs` /
   `evaluation_results` with tokens and cost.

## 5. Under the hood — what the system actually does *(built, not intent)*

### Data model
- **`categories`** stores the definition. User-editable fields (in storage order):
  `slug, owner_email, description, dept_slugs, include_child_orgs, document_type_slugs,
  keywords, inclusion_context, exclusion_context, adjudication_hints_keep,
  adjudication_hints_drop, extra_guidance_urls, only_use_extra_guidance_urls,
  extra_law_urls, only_use_extra_law_urls`. Filter lists (`dept_slugs`,
  `document_type_slugs`, `keywords`) are comma/newline-separated strings, split into
  tokens at query time.
- **`content`** is the corpus (one row per URL); **`page_organisations`** links pages to
  organisation slugs; **`organisations`** holds slug/title.
- The stack is **FastAPI + Jinja**, backend-agnostic via `govuk_corpus/backend.py`
  (SQLite for dev/tests, Postgres in production) using a single `_P` placeholder.

### The deterministic funnel (`govuk_corpus/shortlist.py`)
A single query builder assembles WHERE clauses for the active filters; each funnel stage
is one `COUNT(*)`:

- **Organisation filter** — `EXISTS (SELECT 1 FROM page_organisations po WHERE
  po.page_url = c.url AND po.organisation_slug IN (…))`. "Include child organisations"
  expands the slug set via the organisation hierarchy.
- **Document-type filter** — matches on the **effective** document type
  (`EFFECTIVE_DOCTYPE_EXPR`): an `html_publication` (the body of a publication) is
  classified by its **parent** publication's type, using the materialised
  `content.parent_document_type` where backfilled, otherwise dug live out of the content
  JSON. So picking `guidance` also keeps html_publications whose parent is guidance.
- **Keyword filter** — Postgres uses the GIN-indexed generated column
  `content.search_tsv` with `search_tsv @@ plainto_tsquery('english', <term>)` (stemmed);
  terms are OR-combined for `match = "any"` (the default). `search_tsv` covers
  `title + description + search_text` (the HTML-stripped body). SQLite dev falls back to
  a case-insensitive `LIKE`.

Only non-redirect pages with stored content count (`c.is_redirect = 0 AND
c.content_hash IS NOT NULL`).

### Preview & audit surfaces
- **Preview (`guc-0003`)** renders the funnel (table + log-scaled Sankey), the
  organisation and keyword breakdowns, the "Pages in this shortlist" list
  (`/api/categories/{id}/results-table`), and — for advanced users — the parameter-
  inlined SQL behind every list and stage.
- **Audit shortlist (`guc-0004`)** browses/exports the pages at any stage
  (`shortlist.export_rows`) with selectable columns and CSV/JSON/XLSX download.
- **Counts are cached** in `category_page_counts` keyed by a definition/corpus version,
  so the Categories list and preview read a stored number instead of recomputing;
  **Refresh** forces a recompute.

### The LLM pipeline (`govuk_corpus/evaluate.py`)
Runs only over the shortlist, in three phases:
- **Phase 1 — Inclusion**: judge each shortlisted page against the *include* description
  → keep/drop + score + reason.
- **Phase 2 — Exclusion**: re-check the pages Phase 1 kept against the *exclude*
  description.
- **Phase 3 — Adjudication**: apply the keep/drop hints.

Each **run** fixes one provider/model (set in Settings) and evaluates in capped batches,
resuming where it left off; tokens and cost are recorded per run in `evaluation_runs`,
and per-page decisions in `evaluation_results`. Multiple runs (different models) can be
compared. Daily spend is tracked and guarded against a configurable budget.

### Transparency
Every SQL-backed list exposes its exact query (parameters inlined for readability;
execution still uses safe parameter binding) behind an advanced-only **Show SQL**
disclosure — funnel stages, both breakdowns, the shortlist/pages lists, audit, and the
admin/settings lists.

## 6. Related pages
- Definition form — `guc-0002`
- Category assistant (interview) — `guc-0013`
- Preview & run (funnel, breakdowns, shortlist, AI pipeline) — `guc-0003`
- Audit results (browse/extract per stage) — `guc-0004`
- Categories list — `guc-0001`

See [PAGE_INDEX.md](../PAGE_INDEX.md) for the full page registry, and
[RUNBOOK.md](../RUNBOOK.md) for the nightly corpus cycle and count rebuilds.
