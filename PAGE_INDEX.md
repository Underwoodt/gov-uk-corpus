# Page Index & Page-ID Convention

Every rendered page in the Shortlist Builder carries a **unique page ID** shown in the
**bottom-left corner in mid-grey**, so users can quote it when reporting a problem and
we can pin the report to an exact page.

## The rule

- **Format:** `guc-NNNN` — the prefix `guc-` (GOV.UK Corpus) plus a zero-padded serial
  (`guc-0001`, `guc-0002`, …). One ID per page.
- **Placement:** bottom-left, mid-grey. Rendered centrally — see *How it's wired* below;
  you do **not** hand-position it.
- **Uniqueness:** each ID is used by exactly one page.
- **Retire, never reuse:** when a page is removed, mark its ID **Retired** in the table
  below. A retired ID is never assigned to a new page — always take the next unused
  serial (see *Next free serial*).
- **Every new page gets one.** Adding a page without an ID is a bug.

## Next free serial

**`guc-0016`** ← assign this to the next new page, then increment this line.

## How it's wired (so the rule is automatic)

- The tag is rendered once for all templates by the base layout:
  [`webapp/templates/base.html`](webapp/templates/base.html) has
  `<div class="page-id">{% block page_id %}{% endblock %}</div>`.
- Each page template sets its ID immediately after the `extends` line:
  `{% block page_id %}guc-0003{% endblock %}`.
- The style (fixed, bottom-left, mid-grey `--muted`) lives in
  [`webapp/static/govuk.css`](webapp/static/govuk.css) under `.page-id`.
- Pages **not** built on `base.html` (the inline sign-in and create-account pages in
  [`webapp/app.py`](webapp/app.py)) render `<div class="page-id">guc-NNNN</div>` directly.

**Adding a page (checklist):**
1. Take the *Next free serial* above.
2. Add `{% block page_id %}guc-NNNN{% endblock %}` after the template's `extends`
   line (or the inline `<div class="page-id">` for a non-`base.html` page).
3. Add a row to the table below and bump *Next free serial*.

## Registry

| ID | Page | Template | Route(s) | Status |
|----|------|----------|----------|--------|
| guc-0001 | Categories (home) | `list.html` | `GET /` | Active |
| guc-0002 | Category definition (create / edit) | `form.html` | `GET /categories/new`, `GET/POST /categories/{id}/edit`, `POST /categories/new` | Active |
| guc-0003 | Preview and run | `preview.html` | `GET /categories/{id}` | Active |
| guc-0004 | Audit Results (Shortlist + Dashboard) | `audit_shortlist.html` | `GET /categories/{id}/shortlist` | Active |
| guc-0005 | Run performance | `performance.html` | `GET /categories/{id}/performance` | Active |
| guc-0006 | Run detail | `run_detail.html` | `GET /categories/{id}/runs/{run_id}` | Active |
| guc-0007 | Shortlist results table | `results_table.html` | `GET /categories/{id}/results` | Active |
| guc-0008 | Download shortlist | `download.html` | `GET /categories/{id}/download` | Active |
| guc-0010 | Settings | `settings.html` | `GET /settings` | Active |
| guc-0011 | Peak-hours schedule | `peak_schedule.html` | `GET /settings/peak/{provider}` | Active |
| guc-0012 | AI Assistant | `assistant.html` | `GET /assistant` | Active |
| guc-0013 | Category assistant (guided create) | `category_assistant.html` | `GET /categories/new/assistant` | Active |
| guc-0014 | Sign in | inline in `webapp/app.py` | `GET /login` | Active |
| guc-0015 | Create an account (register) | inline in `webapp/app.py` | `GET/POST /register` (accounts mode) | Active |

## Sub-tab IDs

Sub-tabs within a page carry the page's ID plus a letter (as a `data-tabid`); the active
sub-tab's id shows in light grey in the bottom-left corner of the tab pane
(`<span class="pane-id">`). Keep the letters contiguous and update this list when
sub-tabs change.

| ID | Sub-tab | Page |
|----|---------|------|
| guc-0003a | Keyword Matching | Preview and run (`guc-0003`) |
| guc-0003b | Semantic Match | Preview and run (`guc-0003`) |
| guc-0003c | Funnel Results | Preview and run (`guc-0003`) |
| guc-0004a | Shortlist | Audit Results (`guc-0004`) |
| guc-0004b | Dashboard | Audit Results (`guc-0004`) |

## Retired IDs

| ID | Page | Retired | Note |
|----|------|---------|------|
| guc-0009 | Audit dashboard | 2026-09-16 | Merged into the Audit Results page as the Dashboard sub-tab (`guc-0004b`). |

Never reassign a retired ID. When a page is removed, move its row here (keep the ID,
set the date and a note).
