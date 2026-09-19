# Intent: move from "category" to "building a filtered shortlist of GOV.UK"

**Status:** Draft for review — *not yet implemented.*
**Author:** proposed by Claude; to be verified/edited by Tom, then resubmitted for implementation.

## Why

The tool is the **Content Shortlist Builder**, but its central object is still called a
"category". That word doesn't describe what the user is doing: they are **building a
filtered shortlist of GOV.UK pages** about a topic. This document inventories every
user-facing string that uses the "category" framing (and the neighbouring copy), organised
by page and tab, and proposes replacement wording.

## How to use this document

1. **Decide the core vocabulary first** (next section). Every per-page proposal below is
   derived from it, so changing one term here cascades everywhere.
2. Work down the per-page tables. Each row is **Element · Current · Proposed**. Edit the
   *Proposed* column in place, or add a note. Anything you leave is taken as accepted.
3. When you're happy, resubmit and it will be implemented page by page.

Scope note: this covers **user-facing text** (titles, tab names, headings, hints, buttons,
messages, emails). It does **not** change URLs/routes (`/categories/…`), database tables, or
code identifiers — those are a separate, larger change (needs redirects + migration) and are
listed at the end as an optional Phase 2.

---

## 1. Core vocabulary (decide this first)

The main tension: today **"category"** = the saved definition, and **"shortlist"** = the
pages it produces. If we rename the definition to "shortlist" too, the two collide. The
recommendation resolves this by making **"shortlist"** the single primary object (matching
the product name) and recasting the *output* as **"pages"/"results"**.

| Concept (today) | Current term | **Proposed term** | Notes |
|---|---|---|---|
| The saved definition of filters | category | **shortlist** | "a filtered shortlist of GOV.UK pages" in full, "shortlist" in short |
| The collection of them (home) | categories | **shortlists** | |
| One specific saved item (display) | *the category name* | *the shortlist name* | unchanged value, changed label |
| The rules that define it | definition / filters | **filters** (or "definition") | |
| The pages it currently contains | *the* shortlist | **the pages** / **results** / **matching pages** | frees "shortlist" for the object |
| Duplicating one | copy category | **duplicate shortlist** | |
| The guided builder | category assistant | **shortlist assistant** (or "guided builder") | |
| "run a category" | run | **run the shortlist** | |

**Decision needed:** confirm the object noun.
- **Option A (recommended): "shortlist"** — matches the product name; output becomes "pages/results".
- Option B: **"filter"** — "Create a new filter", "Your filters". Keeps "shortlist" for the output, but reads oddly against "Shortlist Builder".
- Option C: **"topic"** — "Create a new topic". Warmer, but less precise about the filtering.

> Everything below assumes **Option A**. Say the word and I'll re-render the tables for B or C.

---

## 2. Global chrome (every page)

| Element | Current | Proposed |
|---|---|---|
| Browser title suffix | `… — Content Shortlist Builder` | *(keep)* |
| Masthead brand | Content Shortlist Builder | *(keep)* |
| Nav: home link | **Categories** | **Shortlists** |
| Nav: search | GOV.UK search | *(keep)* |
| Nav: AI Assistant / Users / Settings | *(unchanged)* | *(keep)* |
| Back-links across pages | ‹ Categories | ‹ Shortlists |

Per-shortlist sub-navigation (the tab row on the pages below): **Definition · Preview and
run · Audit Results · Run performance · URL check**. Proposed: rename **Definition →
Filters** (it's where you set the filters); keep the rest. *(Confirm: keep "Definition" if
you prefer.)*

---

## 3. guc-0001 — Home / list (`list.html`)

| Element | Current | Proposed |
|---|---|---|
| Page title | Categories | Shortlists |
| H1 | Categories | Shortlists |
| Lede | "A category is a saved set of rules for finding gov.uk pages about one topic. Choose a category to run it or change it, or create a new one." | "A shortlist is a saved set of filters that finds GOV.UK pages about one topic. Choose a shortlist to run or change it, or build a new one." |
| Search label | Search categories | Search shortlists |
| Primary button | Create a new category | **Build a new shortlist** |
| Count line | "N categories" | "N shortlists" |
| Table header | Category | Shortlist |
| Row action | Copy (title: "Duplicate this category's rules into a new draft") | Copy (title: "Duplicate this shortlist's filters into a new draft") |
| Empty state | "No categories yet. Create one to get started." | "No shortlists yet. Build one to get started." |

---

## 4. guc-0002 — Create / edit (`form.html`)

| Element | Current | Proposed |
|---|---|---|
| Page title | Change / Create category | Change / Build shortlist |
| H1 | Create New Category / Change Category | Build a New Shortlist / Change Shortlist |
| Lede | "A category is a saved set of rules for finding gov.uk pages…" | "A shortlist is a saved set of filters that finds GOV.UK pages about one topic. These filters build the shortlist, which you can then run." |
| Assistant nudge | "Prefer to be interviewed? Let the assistant … fill this form in for you." | "Prefer to be interviewed? Let the assistant build the shortlist with you." |
| Section heading | Definition | Filters |
| Field label | Name for this category | Name for this shortlist |
| Fixed-name hint | "The name is fixed once created, so results stay comparable." | *(keep, wording fine)* |
| Save button | Create category / Save changes | **Build shortlist** / Save changes |

Field labels **Organisations / Page Types / Inclusion Keyword(s) / What to Include / What to
Exclude** and their hints don't mention "category" — no change needed (they describe the
filters). *(One exception: keyword hint "search GOV.UK's own Search for these keywords" — fine.)*

---

## 5. guc-0003 — Preview and run (`preview.html`)

| Element | Current | Proposed |
|---|---|---|
| Page title | *name* — preview | *name* — preview |
| Section heading | Selection funnel | *(keep)* |
| Sub-tab a | Keyword Matching | *(keep)* |
| Sub-tab b | AI Pipeline | *(keep)* |
| Sub-tab c | Funnel Results | *(keep)* |
| Sub-tab d | Hybrid Search | *(keep)* |
| Heading | Which organisations matched | *(keep)* |
| Empty state | "No organisations set for this category." | "No organisations set for this shortlist." |
| Heading | Which document types matched | *(keep)* |
| Empty state | "No keywords set for this category." | "No keywords set for this shortlist." |
| Heading | Pages in this shortlist | *(keep — "shortlist" here = the pages, which is fine)* |
| AI pipeline intro | "Ask the AI to judge each shortlisted page against this category's …" | "…against this shortlist's inclusion/exclusion rules." |
| Run History note | "Every run for this category." | "Every run for this shortlist." |
| Funnel Results empty | "No runs yet — evaluate a category first on the Semantic Match tab." | "No runs yet — evaluate the shortlist first on the AI Pipeline tab." *(also fixes stale tab name)* |
| Hybrid intro | "…this category's keywords through the GOV.UK Search API (scoped to this category's organisations and document types)…" | "…this shortlist's keywords … (scoped to this shortlist's organisations and document types)…" |
| Footer link | Download category for a test environment | Download shortlist for a test environment |
| Footer hint | "…reproduce and fix this category." | "…reproduce and fix this shortlist." |
| JS status (×3) | "Shortlist not computed yet — it populates after the nightly refresh or when the category is saved." | "…when the shortlist is saved." |

---

## 6. guc-0004 — Audit Results (`audit_shortlist.html`)

| Element | Current | Proposed |
|---|---|---|
| Page title | *name* — audit results | *(keep)* |
| Sub-tab a | Shortlist | Pages *(or keep "Shortlist")* |
| Sub-tab b | GDS Compliance (Non-LLM) | *(keep)* |
| Heading | Audit shortlist | Audit results *(align with the tab/page name)* |
| Heading | GDS Compliance (Non-LLM) | *(keep)* |

---

## 7. guc-0005 — Run performance (`performance.html`)

| Element | Current | Proposed |
|---|---|---|
| Empty state | "No runs yet — evaluate a category on the …" | "No runs yet — evaluate the shortlist on the …" |

---

## 8. guc-0006 — Run detail (`run_detail.html`)

| Element | Current | Proposed |
|---|---|---|
| H1 | Run details | *(keep)* |
| Comment/logic | "Point the category's active run at …" | *(code comment — no user text; ignore)* |

---

## 9. guc-0007 — Shortlist results table (`results_table.html`)

| Element | Current | Proposed |
|---|---|---|
| Page title | *name* — results | *(keep)* |
| H1 | Shortlist results | Results *(or keep)* |

---

## 10. guc-0008 — Download (`download.html`)

| Element | Current | Proposed |
|---|---|---|
| H1 | Download shortlist | *(keep — this is the pages export)* |
| Back-link | ‹ Back to audit results | *(keep)* |

---

## 11. guc-0013 — Guided builder (`category_assistant.html`)

| Element | Current | Proposed |
|---|---|---|
| Page title | Category assistant | Shortlist assistant |
| H1 | Category assistant | Shortlist assistant |
| Intro | "Answer a few questions and I'll draft a category, then create it and open it for you. Or pick an existing category below to refine it…" | "Answer a few questions and I'll draft a shortlist, then build it and open it for you. Or pick an existing shortlist to refine it…" |
| Refine label | Refine an existing category: | Refine an existing shortlist: |
| Dropdown default | — start a new category — | — start a new shortlist — |
| Button | Create category → | **Build shortlist →** |
| Ready message | "Ready to create this category and open it." / "Ready to save your changes and open the category." | "Ready to build this shortlist and open it." / "Ready to save your changes and open the shortlist." |

---

## 12. guc-0018 — URL check (`url_check.html`)

| Element | Current | Proposed |
|---|---|---|
| Page title | *name* — URL check | *(keep)* |
| Intro | "Keep two lists of gov.uk URLs against this category…" "Both lists are saved with the category." | "…against this shortlist…" "Both lists are saved with the shortlist." |
| Sub-tabs | Should be in / Should not be in / Check Results | *(keep)* |
| Empty state | "No AI run yet for this category — 'Final result' is Out for every URL…" | "No AI run yet for this shortlist — …" |

---

## 13. guc-0021 — Updating / rebuild (`rebuilding.html`)

| Element | Current | Proposed |
|---|---|---|
| Page title | Updating *name* | *(keep)* |
| H1 | Updating "*name*" | *(keep)* |
| Sub-copy | "Applying the saved definition." | "Applying the saved filters." |

---

## 14. guc-0020 — User administration (`admin_users.html`)

| Element | Current | Proposed |
|---|---|---|
| Heading | Import a category | Import a shortlist |
| Hint | "Load a category bundle downloaded from another environment (the 'Download category for a test environment' link…). It recreates the category, its runs… Re-importing the same category overwrites it." | Replace each "category" with "shortlist"; "Download shortlist for a test environment". |

---

## 15. Emails / notifications

No email-sending code exists in the repo (no SMTP/sender found), so there is **no
run-finished email to rewrite**. Note the form hint on guc-0002 — *"We email you when a run
finishes. This is not a login."* — describes a feature that isn't implemented; flag whether
to (a) build the email later, or (b) soften/remove that hint. No "category" wording either way.

---

## 16. Pages with no "category" text (no change)

guc-0010 Settings, guc-0011 Peak hours, guc-0012 AI Assistant, guc-0014 Sign in,
guc-0015 Create an account, guc-0016 Profile, guc-0017 Edit user, guc-0019 GOV.UK search —
their visible copy doesn't use "category". (Back-links to the home page change with the
global nav rename in §2.)

---

## 17. Optional Phase 2 — non-copy references (bigger lift, decide separately)

Not user-facing *text*, but they carry the "category" framing:

- **URLs/routes:** `/categories/…`, `/api/categories/…` appear in the address bar. Renaming
  to `/shortlists/…` needs permanent redirects from the old paths (bookmarks, saved links).
- **Page IDs & registry:** PAGE_INDEX.md labels (e.g. "Categories (home)") — internal, cheap
  to update alongside the copy.
- **Code identifiers / DB:** `categories` table, `cat`/`category` variables, template
  filenames (`form.html`, `category_assistant.html`), function names. Internal only; a large
  mechanical refactor with test coverage — recommend doing *after* the copy is settled, if at all.

**Recommendation:** ship the user-facing copy first (this document), leave routes and code
identifiers as Phase 2 (or never, for the internal ones).
