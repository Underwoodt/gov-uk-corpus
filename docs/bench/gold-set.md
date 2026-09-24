# The gold set: how it is made, what we chose, what it tells us, and where it falls short

The *gold set* is the ground truth for the AI pipeline benchmark: a human verdict, per GOV.UK page,
on whether the page belongs in a category's final shortlist. Everything the benchmark reports
(recall, precision, stability, the Haiku-versus-Sonnet comparison) is measured against it. This
note explains the process, the choices behind it, what the labels buy us, and their limits. The
pre-registered protocol is in [protocol.md](protocol.md).

## 1. What a gold label is

One row in `category_gold_labels` per (category, page):

| Field | Meaning |
|---|---|
| `label` | `in` (belongs in the final shortlist), `out` (does not), `borderline` (a reasonable person could go either way) |
| `rationale` | one line saying why, required for every label |
| `labelled_by`, `labelled_at` | who and when |
| `content_hash_at_label` | the version of the page the labeller saw, so a later edit to the page is detectable |
| `stratum_*` | the page's score band, source (keyword shortlist / GOV.UK Search / both) and document type when it was labelled |
| `seed_origin` | whether the page was on the category's should-include / should-exclude list |
| `sample_run_id`, `sample_stage`, `sample_frac` | if the page was picked by the stage sampler: which run, which stage, what fraction of that stage was sampled |

Labels are facts about pages, not about runs. A label made while looking at one run's outcome is
reused to score every other run and every model.

## 2. Which pages are eligible

The population is the *forwarded set*: exactly the pages a fresh Phase-1 run evaluates for the
category. That is the keyword shortlist (pages matching the category's organisations, document
types and keywords in our corpus) plus the GOV.UK-Search-only pages at or above the relevance
floor. Withdrawn pages, redirects and pages we never fetched are excluded because the pipeline
never sees them either. For Inheritance Tax that is 140 pages.

We deliberately label the forwarded set and not the whole corpus. The benchmark measures the AI
phases, not the keyword filter in front of them. A page the keyword filter never forwards is a
separate question (keyword recall), and one the benchmark cannot answer.

## 3. Two ways to label, one store

**The sheet.** `gold export` writes a CSV of the forwarded set with the latest run's verdicts,
reasons and primary topics as context, in a *stratified order* (score band × source × document
type, round-robin, seeded shuffle inside each stratum) so that labelling top-down covers every
kind of page early. `gold import` validates the whole sheet before writing anything: every URL
must canonicalise and exist in the corpus, every label must be one of the three values, every
labelled row needs a rationale, duplicates are rejected, and a page whose body changed since the
export is flagged as drifted.

**The page (guc-0029).** The Gold labels page shows every page one inclusion run evaluated, with
the Phase-1 and Phase-2 verdicts and reasons and a badge for the stage the page left at:
dropped at Phase 1, dropped at Phase 2, kept to the end, or unparsed. The labeller answers one
question per page, "was the final keep/drop right?", with *correct*, *wrong* or *borderline*
and a one-line reason. The answer is turned into a label from the page's final outcome:

| Final outcome | Correct | Wrong |
|---|---|---|
| kept | `in` | `out` |
| dropped | `out` | `in` |

Borderline is stored as `borderline` either way. An unparsed page has no outcome to judge and
cannot be labelled from the page (it can from the sheet).

Both paths write the same table, so they can be mixed. The page can also download the sheet
pre-filled with whatever has been labelled so far.

## 4. Labelling a minimum set instead of everything

Labelling 140 pages carefully is about five hours. The page lets the labeller set a **target per
stage** and picks that many pages at random within the stage. The picks are fixed by a seed:
the same pages appear on every visit and in every browser, and raising a target only adds pages,
so work is never wasted. The picker is uniform within a stage and ignores the should-include and
should-exclude hints, so the sample is not tilted toward pages we already believe are in.

The defaults are: label a stage in full when it has 25 pages or fewer, otherwise 40. For the
current Inheritance Tax run that is all 10 pages kept to the end, all 7 dropped at Phase 2, and
40 of the 123 dropped at Phase 1: 57 pages, roughly two hours.

### Why the sampling fraction is stored

Sampling stages at different rates changes what the labelled set represents. If every kept page
is labelled but only a third of the Phase-1 drops are, the labelled set over-represents pages the
run kept. Most of the pipeline's misses live among the drops, so recall computed naively on the
labelled pages comes out too high. The fix is to store the sampling fraction with each label and
have the report weight each label by its inverse (a page sampled at one in three stands for
three). The report gives both the raw and the weighted figures and names the fractions used, so
a reader can see how much the sampling moved the numbers. Labels from the sheet carry no
fraction and count once.

## 4a. More than one labeller

Every labeller's vote is stored on its own, keyed by page and labeller; the gold label is a
*consensus* row computed from the votes. One vote stands on its own. When several people have
voted, unanimous or strict-majority wins, and a two-way split becomes `borderline` until someone
adjudicates it. Adjudication sets the final label with a one-line resolution note and never
touches the votes, so the disagreement stays on record.

Best practice, and how the page supports it:

- **Everyone labels the same sample.** The seeded picks are identical for every labeller, so
  agreement can be measured on every page rather than on an accidental overlap.
- **A short calibration round first.** Both labellers do the same ten pages, compare, and write
  down what "in" means for the category. Most disagreement is about the rules, not the pages.
- **Label blind.** Other people's votes on a page stay hidden until you have saved yours. The
  "label blind" switch also hides the model's verdicts and reasons, and asks for the label
  directly (in / out / borderline) instead of "was the run right?". Votes made that way are
  flagged, so the report can say how much seeing the model's answer moves people.
- **Measure agreement, then adjudicate.** The Agreement page gives raw agreement and Cohen's κ
  per pair, Fleiss' κ over everyone, and lists the disagreements with both rationales side by
  side. A κ below about 0.4 means the labelling guide needs work before more labelling.
- **The CSV path is a labeller too.** A sheet imported with a labeller name becomes that
  person's votes; two people can label the same sheet independently and import both.

## 5. Choices we made, and why

- **Three labels, not two.** Category boundaries are genuinely fuzzy. Forcing borderline pages
  into in or out would put noise into the metric that matters most. Instead every accuracy
  figure is reported three ways: on the definite pages only (the headline), with borderline
  counted as in (the recall upper bound), and with borderline counted as out (the precision
  upper bound).
- **A rationale on every label.** It slows labelling slightly but makes the set auditable and
  makes the judgements reusable: when the benchmark says a model missed a page, the rationale
  says what it missed.
- **Show the model's verdict while labelling.** The labeller sees what the pipeline decided and
  why. That risks anchoring, but it roughly halves the time per page and, because the question
  is "was this right?", disagreement is explicit rather than incidental. The protocol declares
  this as a limitation.
- **Rate the final outcome, not each phase.** A page dropped at Phase 2 is judged on whether it
  should be in the final shortlist. Which phase got it wrong is visible in the benchmark's
  Phase-2 value-add figures; it does not need a second human judgement.
- **Seeded randomness everywhere.** The sheet order, the stage picks and the benchmark's
  bootstrap all use fixed seeds. Repeatability was a requirement, and it also means a second
  labeller can be given exactly the same pages.
- **Freeze the category while labelling.** Editing the category's keywords or criteria changes
  the forwarded set and what "in" means. No saves on the category between export and report.
- **Record the page version.** The content hash at labelling time lets the report exclude pages
  that changed between labelling and evaluation instead of silently scoring a different page.

## 6. What we get out of it

With even a partial gold set the benchmark can say, with intervals:

- **Recall of Phase 1** on the definite pages, the primary metric, because the current grounded
  prompt keeps only 17 of 140 pages while 81 are on the category owner's should-include list.
  Recall tells us whether the prompt is dropping pages that belong.
- **Whether Sonnet 4.6 beats Haiku 4.5** on recall by at least five points, tested pairwise on
  the same pages, and at what cost per page recalled.
- **Whether Phase 2 earns its keep**: how many false positives it removes against how many
  true positives it wrongly drops, paired on identical Phase-1 keeps for both Phase-2 models.
- **Run-to-run stability** at temperature 0: which pages flip between repeats, and how much
  the score moves. This is what decides whether production should pin sampling.
- **Calibration**: whether the score bands the UI shows (major focus, discussed a moderate
  amount, mentioned in passing) actually track the share of pages that belong.
- **Grounding**: whether the evidence quotes the model returns are verbatim from the page text
  it was sent, and whether the primary topic is a real description rather than a restatement of
  the criteria.
- **A reusable asset.** The labels outlive this benchmark: every future prompt change or model
  swap can be scored against them in minutes.

## 7. Limitations

- **Agreement depends on a second labeller actually labelling.** The store and the Agreement
  page support several labellers, but until at least two people have covered the same sample
  there is no inter-annotator figure, and we cannot separate labeller error from model error.
- **Anchoring.** By default the labeller sees the model's decision and reason, so disagreements
  are more deliberate than agreements, which may flatter the model slightly. Blind votes are
  flagged and can be compared against sighted ones, but only once there are enough of each.
- **Hidden is not secret.** Other labellers' votes are hidden in the page until you have voted,
  as a discipline, not a security boundary; a determined person could read them from the page
  source.
- **Stratified by one run.** The stage sampler picks pages by where they left *one* reference
  run. Weighting corrects the overall figures, but a page another model keeps that this run
  dropped is only in the gold set if it fell into the Phase-1 sample.
- **Few definite outs.** The keyword filter already removed most obviously irrelevant pages, so
  the forwarded set is in-heavy. Precision and specificity have wide intervals until there are
  15 or so definite `out` labels, and the protocol treats them as descriptive below that.
- **Weighted figures have no intervals.** Bootstrap confidence intervals and the paired tests
  are computed on the raw sample. The weighted figures are point estimates that show the
  direction and size of the sampling bias.
- **Drift is coarse.** The content hash covers the whole page record, so a metadata-only change
  also counts as drift and excludes the page. Safer than the alternative, but it can shrink the
  set over time.
- **Category-specific.** The labels are for Inheritance Tax under its frozen definition. They
  say nothing about other categories, and a change to this category's criteria makes them a
  different question.
- **The keyword stage is out of scope.** Pages the keyword filter never forwards are never
  labelled, so the benchmark cannot measure what the pipeline never sees.
