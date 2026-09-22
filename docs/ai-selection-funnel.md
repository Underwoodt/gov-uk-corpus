# How pages are selected for the AI — the selection funnel

_Worked through with the **Inheritance Tax** shortlist, GOV.UK relevance floor `GOVUK_MIN_ES_SCORE = 0.005`._

This explains every filtering phase, what each number means, and how they should
add up to the set of pages the AI evaluates. It also pins down the one place where
your requested definition of bucket **(a)** differs from what the code does today —
that difference is the reason the on‑screen numbers currently look mixed up.

---

## 1. The two stages of selection

Pages reach the AI through **two independent routes**, then get merged:

```
                          ┌────────────────────────────┐
  Whole corpus  ─────────▶│ 1. Deterministic keyword    │
  (873,990 pages)         │    filter (org → doc type    │──┐
                          │    → keyword)                │  │
                          └────────────────────────────┘  │   merge +
                          ┌────────────────────────────┐  │   de‑dupe
  GOV.UK Search ─────────▶│ 2. GOV.UK augmentation       │──┤────────▶  AI input
  (GOV.UK's own relevance)│    (relevance floor)         │  │
                          └────────────────────────────┘  │
```

- **Route 1 — the corpus keyword filter** is *your* deterministic filter. A page is
  in if it is published by a matching organisation, is a matching document type, and
  contains a matching keyword. No AI, no GOV.UK relevance score involved.
- **Route 2 — GOV.UK augmentation** asks GOV.UK's own Search the same shortlist
  (scoped to your orgs + doc types) and picks up pages your keyword filter missed.
  GOV.UK attaches a relevance score (`es_score`) to each; the **floor** drops the
  weak ones.

Every page in the merged set is tagged by **where it came from**:

| Tag | Meaning |
|---|---|
| **Both** | In your corpus keyword filter **and** returned by GOV.UK Search |
| **Shortlister only** | In your corpus keyword filter; GOV.UK Search did **not** return it |
| **GOV.UK only** | Returned by GOV.UK Search only; your keyword filter missed it |

---

## 2. Phase‑by‑phase (deterministic funnel — Route 1)

These are the rows in the Selection funnel table. Each narrows the one before it:

| Phase | Pages passing | Rejected here |
|---|--:|--:|
| All Pages | 873,990 | — |
| Matched Orgs | 106,835 | 767,155 |
| Matched Doc Type | 3,622 | 103,213 |
| **Contains Keywords** | **137** | 3,485 |

`Contains Keywords = 137` **is the corpus keyword shortlist.** It already contains
both the *Both* pages and the *Shortlister only* pages — they are the same 137 pages,
just tagged by whether GOV.UK also found them.

---

## 3. The three buckets you asked for

You asked to see three buckets that sum to the AI input:

- **A — Shortlister & GOV.UK match** (Both)
- **B — Shortlister only**
- **C — GOV.UK only, above the floor**
- **Total forwarded to AI = A + B + C**

Here are the real counts for this shortlist, split by the `0.005` floor:

| Bucket | Definition | In corpus | ≥ floor (or no score) | Below floor |
|---|---|--:|--:|--:|
| **A — Both** | corpus keyword match **and** GOV.UK returned it | 89 | **42** | 47 |
| **B — Shortlister only** | corpus keyword match, GOV.UK didn't return it | 50 | **50** | 0 |
| **C — GOV.UK only** | GOV.UK returned it, keyword filter missed it | 35 | **1** | 34 |

(Shortlister‑only pages have no GOV.UK score at all, so the floor can't apply to them —
they all pass. For GOV.UK‑only, only **1 of 35** clears the floor: the scores here run
`min 0.00032 · avg 0.0017 · max 0.0062`, so nearly all sit below `0.005`.)

---

## 4. The decision buried in bucket A ⚠️

Your definition of **A** is _"pages in Both **above the floor**"_ → **42**.
But the code **today does not apply the floor to Both pages.** A *Both* page is
forwarded to the AI because it matched **your keyword filter** — GOV.UK's relevance
score is not used to gate it. So there are two possible totals:

### Model 1 — floor only on GOV.UK‑only (what the code does now)

The floor is a gate for pages where **GOV.UK relevance is the only evidence**. Pages
your own keyword filter caught are kept regardless of GOV.UK's score.

| | Pages to AI |
|---|--:|
| A — Both (all) | 89 |
| B — Shortlister only | 50 |
| C — GOV.UK only ≥ floor | 1 |
| **Total to AI** | **140** |

### Model 2 — floor on everything GOV.UK scored (your written definition)

The floor also gates *Both* pages, so 47 keyword‑matched pages get dropped for scoring
below `0.005`.

| | Pages to AI |
|---|--:|
| A — Both ≥ floor | 42 |
| B — Shortlister only | 50 |
| C — GOV.UK only ≥ floor | 1 |
| **Total to AI** | **93** |

**Recommendation:** keep **Model 1**. Model 2 throws away 47 pages that *your own
keyword filter* selected, purely because GOV.UK happened to score them low — the floor
was meant to suppress GOV.UK‑only noise, not to overrule your keyword matches. But this
is your call; if you want Model 2, it's a one‑line change to how the top‑up is built.

---

## 5. Why the numbers on screen currently look mixed up

Three separate things are being shown next to each other, measured at different moments:

1. **`Both 89 + Shortlister 50 = 139`** is the **true, live** corpus keyword shortlist.
   Recomputed against the corpus right now it is **139**, and the GOV.UK‑coverage table
   agrees exactly (139, zero difference).
2. **`Contains Keywords = 137`** is **not** live — it is a **cached** funnel count that
   has gone stale. The funnel caches each stage's count for speed and only recomputes
   when it detects the definition or corpus changed; here the corpus gained 2 matching
   pages (137 → 139) but the cache still shows the old `137`. **Click ↻ Refresh** on the
   Selection funnel and the row updates to **139**, matching `A + B`. (So the earlier
   "snapshot drift" explanation was wrong — the snapshot is in sync; the funnel cache is
   the stale value.)
3. **`+ GOV.UK Search = +13`** (old row, now replaced by `C · GOV.UK only`) is from your
   **last AI run** (150 evaluated − 137
   keyword = 13). That run is historical: at the time it ran, 13 GOV.UK‑only pages
   qualified. Today only **1** does (the pool grew to 35, but the `0.005` floor now
   drops 34 of them). So +13 is not "13 pages qualify now" — it's "13 were fed in that
   run".

So the funnel **flow** is correct and does reconcile:

```
137 (keyword)  +  13 (GOV.UK add, that run)  =  150 evaluated
150 evaluated  →  AI kept 25, dropped 125          (25 + 125 = 150) ✓
25 kept        →  judge kept 16, dropped 9         (16 + 9  = 25)  ✓
```

The **sub‑rows** don't add into the flow because they're a *pool snapshot*, not the
run — which is why mixing them into the same table reads as "not adding up".

---

## 6. Do you need another run?

**Not to make the numbers reconcile** — the flow already reconciles (150 → 25 → 16).

If you want the AI input to be a clean **A + B + C** that equals what a run processes,
do this once, in order:

1. **Click ↻ Refresh on the funnel** so the cached `Contains Keywords` count is
   recomputed (it becomes 139, matching `A + B`). The A/B/C rows are already live.
2. **Create a fresh AI run.** It will pull the current corpus keyword shortlist **plus**
   only the GOV.UK‑only pages that clear the floor today — i.e. exactly **A + B + C**
   under Model 1 (**~140**), not the stale 150.

Note the practical consequence at the current floor: a fresh run adds only **1**
GOV.UK‑only page (34 of the 35 are below `0.005`). If you want more of that pool
considered, **lower the floor** before re‑running — don't raise it.

---

## 7. One‑line summary

- **A + B** = your corpus keyword shortlist (the `Contains Keywords` row).
- **C** = the GOV.UK‑only pages that clear the relevance floor and are in the corpus.
- **A + B + C** = the pages a fresh run forwards to the AI — **140** here under the
  current rules (floor on GOV.UK‑only), or **93** if you decide the floor should also
  gate *Both* pages.
