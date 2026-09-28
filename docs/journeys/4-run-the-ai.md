# Journey 4 — Run the AI over your shortlist

*What this shows: how to actually run the AI pass — start a run, watch it work, and know at a glance
whether it finished cleanly or needs a nudge.*

**Who / when:** anyone who has saved a set of Filter Parameters (Journey 3) and now wants the AI to
judge the pages. This is the core "do it" step — everything after (download, compare, tune) reads
the runs you make here.
**Pages used:** **Runs → Pipeline Runs**, and each run's **Run details** page.

---

## The 30-second pitch

> Say: "Once you've described what you want, one button runs the AI over it. It works on the server,
> so you can walk away and come back — and when you return, the run tells you plainly whether it
> finished, what it cost, and if anything went wrong. You're never left wondering 'did that work?'."

---

## 4.1 — Start a run

**What you do:** open the **Runs** tab. Its first sub-tab, **Pipeline Runs**, is where every run
lives. Click **New Run**. The tool creates a run and opens **its own page**; give the run a **name**
(more than three characters — the **Start Run** button stays greyed out until you do), then press
**Start Run**.

**What you see:** the run starts working **on the server, in the background**. You can leave the page
and come back — it keeps going. A note on the page says as much.

> Say: "New Run, give it a name, Start Run. That's it. It's now running on the server — close the
> tab, make a coffee, come back. The run doesn't stop just because you looked away."

![Screenshot — the Pipeline Runs sub-tab with the New Run button, and the run's own page with the name box and Start Run button (disabled until the name is filled)](screenshots/4-1-new-run.png)

**Why it matters:** every run is a named, saved thing with its own page — not a fire-and-forget
button. You can always come back to exactly this run and see what it did.

---

## 4.2 — What's happening while it runs

**What you do:** watch the live line on the run's page (or on the Pipeline Runs list).

**What you see:** the AI works in **two passes**:

- **Inclusion** — reads every page that passed your filters, scores it **0.0–1.0**, and keeps
  anything above zero (a generous first read).
- **Exclusion** — re-checks only the kept pages and can **only drop** (a strict second read that
  removes the look-alikes).

A live status shows progress — **"evaluated 120 · 60 left"** — and the run moves from Inclusion into
Exclusion on its own. Pages are evaluated several at a time, so it's quicker than it looks.

> Say: "Under the bonnet it's two reads: a generous one that scores and keeps, then a strict one
> that only removes. You just watch the counter — 'evaluated 120, 60 left' — and it rolls from the
> first pass into the second by itself."

![Screenshot — the run's page mid-run: the live "evaluated N · M left" status above the two phases, Inclusion then Exclusion](screenshots/4-2-two-phases-live.png)

**Why it matters:** the split is *why* the shortlist is trustworthy — a keep has survived both a
"does this belong?" and a "is this a look-alike?" test — and you can see it happen, not just a
spinner.

---

## 4.3 — Did it finish? Read the Run outcome

**What you do:** when the run stops, look at the **Run outcome** panel at the top of the run's page.

**What you see:** a one-line verdict with a colour:

- **✅ Completed** (green) — every page was judged; you're done.
- **⚠ Did not complete** (amber) — it stopped for a *benign* reason and just needs continuing:
  usually the **daily budget** was reached, or it hit this run's **page cap** (a run does up to a set
  number of pages at a time). Come back tomorrow or with more budget, or press **Complete run**
  (next step).
- **⛔ Did not complete** (red) — a *real* problem to fix. The commonest is a **low credit balance**,
  which says in as many words: **"Check your Anthropic Credit Balance."** Top up, then re-run.

It also lists any **page-level failures** — replies the model gave that couldn't be read (or were
cut off) — with a link to see them.

On the **Pipeline Runs** list, the same signal appears as a coloured **chip** next to the run's name
— e.g. a red **"low balance"** or amber **"budget reached"** / **"page cap"** — so you can spot a
run that needs attention without opening it.

> Say: "The best bit is you never have to guess whether it worked. Green means done. Amber means it
> paused for a good reason — budget or a page cap — and you just carry on. Red means fix something,
> and it tells you exactly what — right down to 'top up your Anthropic credit'."

![Screenshot — the Run outcome panel: a green "Completed" state, and an inset of the red "low balance" state with the "Check your Anthropic Credit Balance" advice](screenshots/4-3-run-outcome.png)

**Why it matters:** a run that half-finished silently is worse than useless. This turns "did it
work?" into a plain answer and, when it didn't, an instruction.

---

## 4.4 — Nudging a run that stopped short

**What you do:** if the outcome was amber (paused) or some pages failed, use the buttons on the run's
page rather than starting over.

**What you see:**

- **Complete run** — carries on from where it stopped, evaluating the pages it hadn't reached yet.
  Use this after a budget or page-cap pause. (You can also **Stop Run** a run that's mid-flight; it
  resumes cleanly later.)
- **Reprocess failed rows** — in the **Not parsed** section, re-asks the model for just the pages
  whose reply couldn't be read, backing off politely if the provider is rate-limiting. It fixes the
  stragglers without redoing the whole run.

Both pick up where things were left — you never lose the work already done, and you're not billed
twice for pages already judged.

> Say: "If a run pauses, don't start again — Complete run picks up the leftovers. If a handful of
> pages came back garbled, Reprocess failed rows re-asks just those. You only ever pay for the pages
> you haven't already done."

![Screenshot — a partly-finished run: the Complete run button, and the Not parsed section with its Reprocess failed rows button](screenshots/4-4-complete-reprocess.png)

**Why it matters:** runs are resumable and repairable. A stop is a pause, not a write-off.

---

## A power-user aside (advanced interface only)

By default every run evaluates **10 pages at a time** and uses today's prompt wording — you don't
have to touch any of that. If you switch the app to the **advanced** interface, **New Run** gains a
small **Trial (A/B)** panel (prompt variant, how many pages at a time, prompt caching) for
deliberately comparing two set-ups, and a **Run performance** tab appears for the side-by-side
numbers. A basic user needs none of it to get a clean run.

---

## In one breath

> Say: "New Run, name it, Start Run — it runs on the server in two passes, a generous keep then a
> strict drop. When it stops, the outcome panel tells you in one line whether it finished, and if
> not, exactly why and what to do — carry on with Complete run, retry the stragglers with Reprocess
> failed rows, or top up your credit. No black box, no guessing."

## Links

- Previous: [Journey 3 — Create a shortlist](3-create-a-shortlist.md)
- Next: [Journey 5 — Download a shortlist](5-download-a-shortlist.md)
- Go deeper: [Journey 6 — How was this built?](6-how-was-this-built.md) — reading the results,
  comparing runs, and letting the AI sharpen your criteria.
