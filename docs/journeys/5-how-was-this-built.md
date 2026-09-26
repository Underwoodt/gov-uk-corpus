# Journey 5 — "Hang on, how was this built? Is it what I want?"

*What this shows: how to trust a shortlist — see exactly how the Filter Parameters generated it,
which pages were kept or dropped and why, what the AI decided, and how the AI can help you sharpen
the question.*

**Who / when:** anyone about to *rely* on a shortlist — before you hand it on or make a call from it.
**Pages used:** **Shortlist** (Selection funnel + stages) · **Filter pipeline run** (Run history +
Run details) · **Data Analysis**.

---

## The 30-second pitch

> Say: "The best thing about this tool isn't the list — it's that the list shows its working. You
> can watch it narrow from all of GOV.UK down to your pages, open any single page and read why it
> was kept or dropped, see what the AI actually decided, and — if the answer isn't quite right — let
> the AI suggest a sharper question. Nothing is a black box."

---

## 5.1 — Showing how it works: the funnel

**What you do:** on the **Shortlist** tab, look at the **Selection funnel** table.

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

**Why it matters:** the funnel turns "trust me" into "look" — you can see which single filter did
the heavy lifting, and whether the drop at each step matches your intent.

---

## 5.2 — Seeing what was kept and dropped, and why

**What you do:** still on the **Shortlist** tab, switch the **stage** view to look at the pages a
given step kept or dropped, and open the AI's verdict on individual pages.

**What you see:** rows with the page, and — for the AI stages — whether it was **kept or dropped**, a
**score**, and a **reason** in plain English (plus the evidence it leaned on). You can answer "why
this page?" for any row.

> Say: "Pick a stage and you can read the actual decisions. For the AI steps you get a keep-or-drop,
> a confidence score, and a one-line reason — 'kept: sets out slurry storage standards', 'dropped:
> slurry here means concrete'. If you disagree with a call, you've just found something to fix in the
> criteria."

**Why it matters:** a shortlist you can audit row by row is one you can defend — and every
disagreement you spot is a concrete edit for the include/exclude criteria or a new Keep/Drop example.

---

## 5.3 — Seeing the AI results (the two-phase pipeline)

**What you do:** open the **Filter pipeline run** tab. Run the AI over the shortlist with **Execute
Active Run**, and use **Run History** to see every run. Open a run to reach its **Run details** page.

**What you see:**

- **The two phases** — **Inclusion** (scores every page 0.0–1.0 and keeps any positive score) then
  **Exclusion** (re-checks the keeps and can only drop) — with pages, keep/drop counts, tokens and
  cost for each.
- **A Run outcome panel** at the top of Run details that answers, at a glance: *did it complete?*
  If not, *why* — a red banner names a real problem (for example a low credit balance says
  **"Check your Anthropic Credit Balance"**), amber flags benign reasons like the daily budget or
  the per-run page cap. It also lists any **page-level failures** — replies that couldn't be parsed
  or were cut off.
- **Per-page results** — the same keep/drop/score/reason as the shortlist stages, for this run.

**What you see in Run History:** each run with its status, and a chip if it stopped early (e.g. a
red **"low balance"** chip) so you can tell a clean run from one that needs attention or a re-run.

> Say: "Running the AI is two passes: a generous first read that scores and keeps, then a strict
> second read that only removes. Every run is kept in the history with its cost and outcome, and if
> one stops short the page tells you plainly why — right down to 'top up your Anthropic credit'.
> No guessing why a run didn't finish."

**Why it matters:** you're not trusting a single opaque score. You can see both passes, what each
cost, whether the run actually finished, and exactly what failed if it didn't.

---

## 5.4 — Letting the AI reframe your question

Sometimes the shortlist is *nearly* right but wobbles — the same borderline pages flip between runs.
The tool can turn the AI on *itself* to fix that.

**What you do:** open the **Data Analysis** tab. Pick two runs to compare, then **Review the
prompts**. The AI reads how the two runs diverged and the pages that flipped, and proposes sharper,
paste-ready **inclusion / exclusion criteria** and **Keep / Drop examples**.

**What you see:** a diagnosis of what's driving the wobble, plus concrete suggested wording. An
**Apply to shortlist** card lets you tick the suggestions you accept and apply them straight to the
shortlist's **Filter Parameters** — then it drops you back at the **Active Run** to re-execute. (A
suggestion that matches what you already have is greyed out, so you never overwrite a good prompt
with an identical one.)

> Say: "Here's the clever bit. If two runs disagree, ask the AI *why*, and it'll hand you tighter
> wording and the exact Keep/Drop examples that would settle the coin-flips. Tick the ones you like,
> apply them, and it takes you straight back to re-run. The tool helps you ask a better question —
> and then re-generates the shortlist from it."

**Why it matters:** this closes the loop from Journey 3 — the coin-flip examples you could write by
hand, the AI can now suggest from real evidence, so the shortlist gets steadier every pass.

---

## In one breath

> Say: "Before you rely on a shortlist: read the funnel to see how the parameters narrowed it, open
> pages to see what was kept or dropped and why, check the AI run finished cleanly, and — if it
> wobbles — let the AI suggest a sharper question and re-generate. The whole thing shows its working."

## Links

- Previous: [Journey 4 — Download a shortlist](4-download-a-shortlist.md)
- Back to the start: [Journey 2 — What is a shortlist?](2-what-is-a-shortlist.md)
- The criteria you'll be tuning live in [Journey 3.4](3-create-a-shortlist.md#34--prompts-include-exclude-and-sorting-out-the-coin-flips-llm-inference).
