# Journey 6 — "Hang on, how was this built? Is it what I want?"

*What this shows: how to trust a shortlist — see exactly how the Filter Parameters generated it,
which pages were kept or dropped and why, what the AI decided, and how the AI can help you sharpen
the question.*

**Who / when:** anyone about to *rely* on a shortlist — before you hand it on or make a call from it.
**Pages used:** **Results** (kept/dropped stages) · **Runs** (Pipeline Runs · Selection funnel ·
Run details) · **Compare Runs**.

---

## The 30-second pitch

> Say: "The best thing about this tool isn't the list — it's that the list shows its working. You
> can watch it narrow from all of GOV.UK down to your pages, open any single page and read why it
> was kept or dropped, see what the AI actually decided, and — if the answer isn't quite right — let
> the AI suggest a sharper question. Nothing is a black box."

---

> **On the simple interface** you see everything in this journey — Pipeline Runs, the Selection
> funnel, Compare Runs and each run's details. Two power-user extras stay hidden until you switch to
> the **advanced** interface: the **Run performance** metrics tab, and the **Trial (A/B)** run
> options (prompt variant, concurrency, prompt caching) on New Run. You need neither to build, run,
> check and tune a shortlist.

## 6.1 — Showing how it works: the funnel

**What you do:** open the **Runs** tab, then its **Selection funnel** sub-tab, and look at the
funnel table (the **Keyword Matching** breakdowns — which organisations, page types and terms
matched — are folded in lower down the same tab).

**What you see:** the page count dropping stage by stage, in the exact order the Filter Parameters
were applied:

- **All pages** — the whole corpus.
- **After organisation filter** — only the owner you picked.
- **After document-type filter** — only the page types you picked.
- **After keyword filter** — only pages containing your keywords.
- **…then the AI pass** — inclusion scores and keeps, exclusion re-checks and drops.

> Say: "This is the shortlist's autobiography. Start at all of GOV.UK, and each filter you set knocks
> the number down — organisation, then page type, then keywords — until the AI takes the survivors
> and makes the judgement calls. If a stage cuts far more or far less than you expected, that's your
> clue something in the Filter Parameters needs a tweak."

![Screenshot — the Selection funnel table on the Runs → Selection funnel sub-tab: counts for All pages → After organisation → After document-type → After keyword → the AI pass](screenshots/6-1-funnel.png)

**Why it matters:** the funnel turns "trust me" into "look" — you can see which single filter did
the heavy lifting, and whether the drop at each step matches your intent.

---

## 6.2 — Seeing what was kept and dropped, and why

**What you do:** on the **Results** tab, switch the **stage** view to look at the pages a
given step kept or dropped, and open the AI's verdict on individual pages.

**What you see:** rows with the page, and — for the AI stages — whether it was **kept or dropped**, a
**score**, and a **reason** in plain English (plus the evidence it leaned on). You can answer "why
this page?" for any row.

> Say: "Pick a stage and you can read the actual decisions. For the AI steps you get a keep-or-drop,
> a confidence score, and a one-line reason — 'kept: sets out slurry storage standards', 'dropped:
> slurry here means concrete'. If you disagree with a call, you've just found something to fix in the
> criteria."

![Screenshot — a stage view on the Results tab: rows of pages with keep/drop, score and a plain-English reason](screenshots/6-2-kept-dropped-rows.png)

**Why it matters:** a shortlist you can audit row by row is one you can defend — and every
disagreement you spot is a concrete edit for the include/exclude criteria or a new Keep/Drop example.

---

## 6.3 — Seeing the AI results (the two-phase pipeline)

**What you do:** open the **Runs** tab (its first sub-tab, **Pipeline Runs**). Click **New Run** —
it creates a run and opens that run's own page, where you give it a name and press **Start Run**.
The run works on the server in the background, so you can leave the page and come back. Every run
you've made is listed in the table on **Pipeline Runs**; open any run's name to return to its
details.

**What you see on a run's page (Run details):**

- **A Run outcome panel** at the very top that answers, at a glance: *did it finish?* If not, *why*
  — a **red** banner names a real problem (for example a low credit balance reads **"Check your
  Anthropic Credit Balance"**), an **amber** one flags a benign reason like the daily budget or the
  per-run page cap. It also lists any **page-level failures** — replies that couldn't be parsed or
  were cut off.
- **The two phases** — **Inclusion** (scores every page 0.0–1.0 and keeps any positive score) then
  **Exclusion** (re-checks the keeps and can only drop) — with pages, keep/drop counts, tokens and
  cost for each.
- **Per-page results** — the same keep/drop/score/reason as the Results stages, for this run.
- If a run stopped part-way, a **Complete run** button carries on where it left off; if some pages
  failed to parse, a **Reprocess failed rows** button re-asks the model for just those, backing off
  on rate limits.

**What you see on the Pipeline Runs list:** every run with its status and cost, and a coloured
**chip** if it stopped early — for example a red **"low balance"** chip, or amber **"budget
reached"** / **"page cap"** — so you can tell a clean run from one that needs attention or a re-run.
While a run is working, a live line shows **"still evaluating… N done · M left"**.

![Screenshot — the Pipeline Runs table: several runs with status, cost, and a red "low balance" stop chip on one](screenshots/6-3a-run-history.png)

> Say: "Running the AI is two passes: a generous first read that scores and keeps, then a strict
> second read that only removes. You start one with New Run, it runs on the server, and every run is
> kept in the list with its cost and outcome. If one stops short, the run's page tells you plainly
> why — right down to 'top up your Anthropic credit' — and a click carries on, or re-tries just the
> pages that failed. No guessing why a run didn't finish."

![Screenshot — the Run details page: the Run outcome panel at the top (status + why it didn't finish + any failures) above the two LLM phases (Inclusion → Exclusion) with pages, keep/drop, tokens and cost](screenshots/6-3b-run-details.png)

**Why it matters:** you're not trusting a single opaque score. You can see both passes, what each
cost, whether the run actually finished, and exactly what failed if it didn't — and fix it without
starting over.

---

## 6.4 — Letting the AI reframe your question

Sometimes the shortlist is *nearly* right but wobbles — the same borderline pages flip between runs.
The tool can turn the AI on *itself* to fix that, and it offers it in two places.

**On the Compare Runs tab:** open **Runs → Compare Runs**. Pick a **baseline run** and it's set
against your recent runs, so you can see how repeatable they are — the score spread and where the
Inclusion and Exclusion phases agree or diverge (each card has an **Explain this (AI)** button for a
plain-English read). Then **Review the prompts**: the AI reads how the runs diverged and the pages
that flipped, and proposes sharper **inclusion / exclusion criteria** and **Keep / Drop examples**.
An **Apply to shortlist** card lets you tick the suggestions you accept, applies them straight to
the shortlist's **Filter Parameters**, and drops you back at **Pipeline Runs** to start a fresh run.
(A suggestion that matches what you already have is greyed out, so you never overwrite a good prompt
with an identical one.)

**On the Filter Parameters tab:** the same review lives here too, as the **"Do you want to try to
improve your prompt with AI?"** card — but here each suggestion carries an **Apply to field** button
that writes the wording straight into its box. It even works after a **single** run (it critiques
that one run on its own), so you can tighten the prompt before you've built up a run history.

Either way, treat the suggestions as **inspiration, not gospel** — the card says exactly that:
review the wording, keep what carries your intent, and Save.

> Say: "Here's the clever bit. Ask the AI *why* your runs disagree and it hands you tighter wording
> and the exact Keep/Drop examples that would settle the coin-flips. On Compare Runs you tick the
> ones you like and it takes you straight to a new run; on Filter Parameters you drop each one
> straight into its box. The tool helps you ask a better question — then re-generates the shortlist
> from it."

![Screenshot — Compare Runs: the AI prompt-review output (diagnosis + suggested include/exclude criteria and Keep/Drop examples) and the "Apply to shortlist" card with accept/skip ticks](screenshots/6-4-prompt-review-apply.png)

**Why it matters:** this closes the loop from Journey 3 — the coin-flip examples you could write by
hand, the AI can now suggest from real evidence, so the shortlist gets steadier every pass.

---

## In one breath

> Say: "Before you rely on a shortlist: read the funnel to see how the parameters narrowed it, open
> pages to see what was kept or dropped and why, check the AI run finished cleanly, and — if it
> wobbles — let the AI suggest a sharper question and re-generate. The whole thing shows its working."

## Links

- Previous: [Journey 5 — Download a shortlist](5-download-a-shortlist.md)
- Back to the start: [Journey 2 — What is a shortlist?](2-what-is-a-shortlist.md)
- The criteria you'll be tuning live in [Journey 3.4](3-create-a-shortlist.md#34--prompts-include-exclude-and-sorting-out-the-coin-flips-llm-inference).
