# Plain-English (GDS) audit — reference

The corpus audit scores each page's body text against the GOV.UK style guide's
plain-English rules and turns the result into a **1–5 star quality rating**
(5 = healthy, 1 = poor). This is the source of truth for the checks, the weights, the
scoring, and where things live. The Audit Results → **Dashboard** tab renders the same
description and weights table (its collapsible "What these checks mean" panel).

Implementation: [`govuk_corpus/readability.py`](../govuk_corpus/readability.py) (the
scan), [`build_readability.py`](../govuk_corpus/build_readability.py) (the corpus
backfill), [`audit_stats.py`](../govuk_corpus/audit_stats.py) (per-stage aggregation).

## The check classes

Each **class** of problem is counted per page. Classes are declared once in
`readability.CHECKS` — adding one is a single list entry.

| Class | Weight | What it flags | Why it matters |
|-------|:-----:|---------------|----------------|
| Words to avoid | 2 | GOV.UK "words to avoid" (utilise, leverage, robust, …) | Jargon / management-speak |
| Phrases to avoid | 1 | Wordy officialese ("in order to", "going forward", …) | Padding |
| Over-long sentences | 3 | Sentences over 25 words | Biggest comprehension hit |
| Nominalisations | 3 | Noun-forms of verbs (implementation, completion, …) | Makes text abstract |
| Vague language | 2 | some, many, often, usually, approximately, several, etc. | Imprecise |
| Unnecessary words | 1 | very, really, basically, "it should be noted that", … | Filler |
| There is / there are | 1 | "there is", "there are" | Usually padding |
| Impersonal "It is" | 2 | "it is required/necessary/… that" | Hides who must act — use "you must" |
| Government-focused (we/us/our) | 2 | we, us, our | Writes for the department, not the user |
| Applicant vs you | 3 | applicant(s) | Third-person — address the reader as "you" |
| Negative phrasing | 2 | "you must not", "you should not", "you shouldn't" | Harder to follow — phrase positively |

Matching is case-insensitive and word-boundary-aware. **"It is"** is deliberately
narrowed to the impersonal templates above (a literal "it is" is far too common to be
useful). See the code for the exact term lists.

## Scoring

1. **Per-class count** — every occurrence in the page body.
2. **Impact** = `Σ weight × log2(1 + count)` over the classes. The log gives
   *diminishing returns*: a few serious issues outweigh many trivial ones, and no single
   high-frequency class (e.g. "we/us/our") can swamp the score. Weights: **3 = high
   impact, 2 = medium, 1 = low**.
3. **Star rating** — impact is normalised by length into a **density** (impact per 1,000
   words), and density maps to stars via fixed bands. Length-normalising makes a short
   page and a long guide comparable. Pages under 40 words are **not** rated (`stars = None`).

```
worked example: nominalisation (weight 3) ×2  →  3 × log2(3) = 4.7
                impersonal "it is" (weight 1) ×10 → 1 × log2(11) = 3.5
                → the 2 serious issues outweigh the 10 trivial ones
```

### Star bands (needs calibration)

`readability.STAR_BANDS` maps density → stars. The current values are **initial
estimates**:

| Density (impact / 1,000 words) | Stars |
|---|:---:|
| ≤ 10 | 5 |
| ≤ 20 | 4 |
| ≤ 35 | 3 |
| ≤ 55 | 2 |
| > 55 | 1 |

**Calibrate them from the corpus.** After the first full re-scan, `build_readability`
prints the star histogram; adjust the bands so pages spread sensibly across 1–5, then
freeze them. Bands are *fixed* (not percentile) so a page's rating is stable over time —
re-banding never requires re-scanning text, only re-deriving stars from the stored counts.

## What's stored (per `content` row)

- `gds_checks` — JSON `{"words": N, "counts": {class_key: count}}` — **source of truth**.
- `gds_english_score` — the weighted **impact** (higher = worse).
- `gds_stars` — the 1–5 rating (NULL if too short).
- `gds_findings` — a readable class-level summary ("Words to avoid: 3; …").
- `reading_age` — UK reading age (Flesch-Kincaid + 5); shown alongside, not part of the star.

Because the raw per-class counts are stored, weights and bands can be re-tuned by
re-deriving impact/stars from `gds_checks` — **no text re-scan needed**.

## Running the scan

```bash
set -a; . ~/gov-uk-corpus.env; set +a               # Postgres
python3 -m govuk_corpus.build_readability --rescan  # full re-scan (after changing checks/weights)
python3 -m govuk_corpus.build_readability            # only un-scanned rows
```

`--rescan` reprocesses every page; without it only rows with no score yet are scanned.
It prints per-batch progress and a final star histogram for calibration.

## Dashboard

Audit Results → **Dashboard**, per funnel stage: the average star rating, average
reading age and page size, a freshness histogram, and the **top issue classes by
impact** (Class | Count | Impact | Why it matters), so the classes dragging the rating
down surface first. The collapsible panel mirrors this document.

## Extending

Add a `Check(...)` to `readability.CHECKS` (key, name, weight, reason, and `terms=` /
`regex=` / `long_sentence=True`). Everything else — storage, impact, stars, dashboard,
the collapsible table — picks it up automatically. Re-scan to populate the new class.
