# Pipeline quality benchmark — protocol (pre-registered)

**Status:** pre-registered before any benchmark run. Any change to this document after the
first `bench run` must be recorded in the *Deviations* section of the results, with the git SHA.

**Question.** How well does the two-phase AI pipeline (Phase 1 inclusion → Phase 2 exclusion)
reproduce a human's shortlist for one category, how stable is it run-to-run, and is Sonnet 4.6
worth its price over Haiku 4.5 — in either phase?

## 1. System under test

| Item | Value | Where it is stamped |
|---|---|---|
| Code | git SHA at run time (`prompts.template_version()`) | `evaluation_runs.prompt_spec.template_version` |
| Phase-1 template | `evaluate.DEFAULT_INCLUSION_TEMPLATE`, fingerprint `e5a2e3f5` at pre-registration | `prompt_spec.template_hash` |
| Phase-2 template | `evaluate.DEFAULT_EXCLUSION_TEMPLATE`, fingerprint `1ae99a31` at pre-registration | `prompt_spec.template_hash` |
| Body limit | 20 000 characters (`evaluate.BODY_CHAR_LIMIT`) | `prompt_spec.body_limit` |
| Prompt variant | `current` (no prompt caching) | `prompt_spec.prompt_variant`, `caching=false` |
| max_tokens | 4096 (`AI_EVAL_MAX_TOKENS`) | `prompt_spec.max_tokens` |
| Sampling | `temperature=0`, thinking omitted (off on both models) | `prompt_spec.temperature`, `prompt_spec.thinking` |
| Concurrency | 4 pages in flight | `prompt_spec.concurrency` |
| SDK | pinned `anthropic[bedrock]>=0.40,<1.0` | `prompt_spec.sdk_version` |

The report cross-checks every run's `template_hash` against the value above; a mismatch
invalidates the run.

## 2. Sample

- **Category:** Inheritance Tax, `category_id 1789943869717`. Its definition (organisations,
  document types, keywords, inclusion/exclusion text, hints) is **frozen** from the gold export
  until the report is published: no saves on this category.
- **Population:** the *forwarded set* — exactly the pages a fresh Phase-1 run evaluates: the
  keyword shortlist (sources `both` + `shortlister`) plus GOV.UK-Search-only pages at or above
  the relevance floor `GOVUK_MIN_ES_SCORE = 0.005` (source `search`). At export: **140 pages**
  (77 `both`/score-0, 44 `shortlister`/score-0, 1 `search`, 1 speech, the rest scored ≥ 0.1 by
  the latest run). Excluded: the below-floor GOV.UK-only pages (34 at planning time), withdrawn
  pages, redirects, unfetched pages.
- **Gold labels:** one labeller (the category owner) labels every forwarded page `in` / `out` /
  `borderline` with a one-line rationale, in the stratified sheet order (score band × source ×
  document type, round-robin). The labeller sees the latest run's verdict and reason (the sheet
  is a review aid, not a blind label; this is recorded as a limitation). Seed lists
  (`should_include_urls` / `should_exclude_urls`) are shown as `seed_label` but never written as
  labels. Import validates the whole sheet (`gold import`) and stamps `content_hash_at_label`.
- **Exclusions at analysis:** pages whose `content_hash` at evaluation differs from
  `content_hash_at_label` (drift) and pages withdrawn since labelling are dropped from accuracy
  metrics and listed.
- **Gold fingerprint:** `gold_sha` (sha256 of url+label lines, 16 hex) is stamped into every
  bench run's `prompt_spec.scope.sha`; runs against a different gold are not pooled.

## 3. Conditions

| Arm | Phase 1 model | Phase 2 model |
|---|---|---|
| haiku→haiku | claude-haiku-4-5-20251001 | claude-haiku-4-5-20251001 |
| haiku→sonnet46 | claude-haiku-4-5-20251001 | claude-sonnet-4-6 |
| sonnet46→haiku | claude-sonnet-4-6 | claude-haiku-4-5-20251001 |
| sonnet46→sonnet46 | claude-sonnet-4-6 | claude-sonnet-4-6 |

- Both models at `temperature=0`, no thinking (symmetric; both accept these parameters).
- **Shared Phase 1:** each repeat runs Phase 1 once per model; both Phase-2 models are then run
  off the *same* Phase-1 run, so the Phase-2 comparison is paired on identical keeps.
- **5 repeats** per Phase-1 model (run names `bench/p1-<m>/r<k>`, `bench/p1-<m>-p2-<m2>/r<k>`).
- Fixed page order (gold URLs sorted); resume-safe (a crashed repeat continues, never duplicates).
- Bench runs never touch the category's `active_run` or auto-advance.

## 4. Hypotheses

Only **H1** is confirmatory; the rest are descriptive and reported with intervals.

- **H1 (primary).** Sonnet 4.6 Phase-1 recall on the definite set D minus Haiku 4.5 Phase-1
  recall ≥ +0.05, with the 95 % bootstrap CI of the difference excluding 0.
- **H2 (stability).** Unanimous per-page Phase-1 agreement across 5 repeats ≥ 95 % (Haiku) and
  ≥ 90 % (Sonnet 4.6).
- **H3 (Phase-2 value).** Net value-add (FP removed − TP wrongly dropped) > 0, and TP wrongly
  dropped ≤ 2 % of gold-in pages.
- **H4 (grounding).** G1 (parsed), G2 (`keep == score>0`), G3 (verbatim evidence) each ≥ 98 %.
- **H5 (calibration).** Precision is monotone non-decreasing across score bands.
- **H6 (Phase-2 model, paired on identical Phase-1 keeps).** Sonnet-P2 net value-add > Haiku-P2
  with TP-wrongly-dropped no higher.

## 5. Metrics and analysis plan

Definitions are implemented in `govuk_corpus/bench_metrics.py` (pure functions, unit-tested).

- Per gold page and run: `p1 ∈ {1,0,∅}` (∅ unparseable), `p2 ∈ {1,0,∅,–}` (– not reached),
  end-to-end `e = 1 iff p1=1 ∧ p2=1`. ∅ counts as 0 for keep/recall and is reported separately.
- **Definite set D = {in, out}.** Every accuracy metric is reported three ways: on D (headline),
  borderline→in (recall upper bound), borderline→out (precision upper bound).
- Per arm × phase × repeat: TP/FP/FN/TN → precision, recall, F1, specificity. **Primary metric:
  Phase-1 recall on D.** Phase-2 conditional on Phase-1 keeps; Phase-2 value-add = FP removed,
  TP wrongly dropped, net, drop precision.
- Pooled: micro over repeats; mean ± SD across repeats; majority vote (≥3/5) as the ensemble.
- Stability: per-page keep agreement, flip pages, unanimous %, Fleiss' κ (pages × 5 raters × 2
  categories), score SD (mean; share > 0.15), confidence-band stability.
- Calibration: per score band, proportion gold-in (D + both bounds); monotonicity; Brier score.
- Grounding G1–G7 pass rates (see plan): parsed; keep↔score; evidence verbatim in the text sent
  (NFKC, whitespace-collapsed, curly→straight quotes, body truncated to 20 000 chars); where_hit
  fields valid and each contains a quote; primary_topic ≤ 10 words and not a copy of the INCLUDE
  text; score ∈ [0,1]; `stop_reason ≠ max_tokens`.
- Cost/latency from `evaluation_runs`: per-page median/p90 ms; cost per correct decision; cost
  per gold-in page recalled.
- Paired comparison on the same pages: decision per model = majority vote; exact McNemar on
  discordant pairs (Binomial(b+c, ½)); bootstrap B = 2000, `random.Random(20260924)`, percentile
  95 % CIs on recall/F1 and their differences; Cohen's h; Cohen's κ between models.
- If `out` < 15 in D, precision and specificity are descriptive only (CIs too wide).

## 6. Stopping rules

- Daily budget (`ai_daily_budget`, raised to $40 for the run window, restored after): each wave
  checks spend; on breach the run is marked stopped and resumes next day.
- > 10 % unparseable replies in any run → halt and investigate before continuing.
- `MAX_CONSEC_EVAL_ERRORS` (6) consecutive provider errors → the run bails (existing behaviour).
- Any `template_hash` or `gold_sha` mismatch → the run is excluded.

## 7. Decision rules

- Phase-1 model: Sonnet 4.6 if H1 holds and cost per gold-in page recalled is < 2× Haiku's;
  otherwise Haiku.
- Phase-2 model: per H6, paired.
- Whether to pin `temperature=0` in production: if pinned stability (H2) is materially better
  than the historical unpinned run-to-run variance on guc-0005 → ADR to pin.

## 8. Out of scope

DeepSeek; Sonnet 5 (rejects sampling parameters and thinks by default — a separate study);
the cached prompt variant; prompt edits; thinking-enabled arms; the below-floor GOV.UK-only
pages (available via `gold export --include-below-floor`).

## 9. Limitations (declared up front)

- Single labeller; no inter-annotator agreement. Borderline handling bounds the effect.
- The labelling sheet shows the latest run's verdict (anchoring risk) — chosen so the ~5 h of
  labelling is feasible; the rationale column is the mitigation.
- `content_hash` covers the whole content JSON, so drift is a superset signal (a metadata-only
  change also counts as drift).
- Few `out` pages expected (Phase 1 was historically permissive) → precision CIs wide.
