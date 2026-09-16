# 0001 — Plain-English (GDS) audit: class-based, weighted, star-rated

- **Status:** accepted (2026-09-16)
- **Reference:** [docs/gds-audit.md](../gds-audit.md)

## Context

The corpus already scored each page for GOV.UK "words to avoid", wordy phrases, and
over-long sentences, reporting a raw issue **count** and listing the individual words
found. We wanted to (a) widen the audit with more plain-English checks, (b) report by
**class of problem** rather than by individual word, and (c) express the result as an
intuitive quality metric rather than "number of problems".

## Decisions

1. **Report by class, not word.** Each check is a named class (e.g. "Nominalisations")
   with a count; individual words are no longer surfaced as issues.
2. **Widen the checks** to: words/phrases to avoid, over-long sentences, nominalisations,
   vague language, unnecessary words, "there is/are", impersonal "It is", we/us/our,
   applicant-vs-you, and negative phrasing. Term lists live in `readability.CHECKS`.
3. **Narrow "It is"** to impersonal templates ("it is required/necessary/… that").
   A literal "it is" fires constantly and is usually fine.
4. **Weighted impact, not raw count.** `impact = Σ weight × log2(1 + count)`. The log
   gives diminishing returns so a few serious issues outweigh many trivial ones, and no
   single high-frequency class dominates. Weights: 3 = high, 2 = medium, 1 = low.
5. **1–5 star quality rating (5 = healthy).** Impact is **length-normalised** (per 1,000
   words) into a density, banded into stars. Bands are **fixed** (not percentile) so a
   page's rating is stable and comparable over time; short pages (< 40 words) are unrated.
6. **GDS-only star for now.** Reading age is shown alongside but not folded into the
   star; it can be promoted to a composite later.
7. **Store raw per-class counts as JSON** (`content.gds_checks`) as the source of truth,
   plus derived `gds_english_score` (impact) and `gds_stars`. This lets weights and star
   bands be re-tuned by re-deriving from the counts — without re-scanning text.
8. **Full corpus re-scan** to populate the new breakdown (`build_readability --rescan`).
9. **Docs home:** `docs/` for feature references + `docs/decisions/` for ADRs like this.

## Consequences

- The headline meaning changed from "issue count" (higher = worse) to a **star rating**
  (higher = better); the dashboard leads with it and explains via the class table.
- Star **bands are initial estimates** and must be calibrated from the first full
  re-scan's density distribution (the backfill prints a star histogram), then frozen.
- Adding a future check is one entry in `readability.CHECKS`; storage, scoring, and the
  dashboard pick it up automatically. Changing checks/weights needs a re-scan (or, for
  weight/band-only changes, a re-derive from stored `gds_checks`).
