# Journey 2: What is a shortlist?

*What this shows: the one idea the whole tool is built around, before anyone touches a form.*

**Who / when:** a first-time user, or a stakeholder, who needs the mental model.
**Pages used:** the Shortlists home page (`/`) and, for orientation, any existing shortlist's tabs.

# Journey Overview

GOV.UK is enormous, and most of the time you don't want all of it. You want the pages about one
thing, owned by the right people, that you can defend to someone else. That list is a **shortlist**.
You don't pick the pages by hand: you set its **Filter Parameters** (organisations, document types,
keywords, and some plain-English guidance for the AI), and the tool generates the list. Filters do
the quick narrowing, an AI pass makes the judgement calls, and because it's a saved definition you
can re-run it, download it, and show how it was built.

---

## The idea in plain terms

> "So a shortlist isn't a one-off search you lose when you close the tab. It's a saved,
> re-runnable definition: filters for the easy narrowing, an AI pass for the judgement, and it always
> shows how it got there."

A shortlist is the list of GOV.UK pages that your **Filter Parameters** generate from the **corpus**,
our large local copy of GOV.UK. You never add pages by hand; you describe what you want and the tool
builds the list.

The parameters work in two halves. First, the filters do the mechanical narrowing: which
organisation owns the page, what document type it is, and which keywords it must contain. These are
deterministic, so the same parameters over the same corpus give the same shortlist every time. Then
the AI judges what's left: it reads each surviving page and decides whether it genuinely belongs,
with a score and a reason. That's how "mentions slurry once in passing" gets separated from "is
actually about slurry".

Keeping the two apart means the filters stay fast and exact while the AI only has to judge the pages
that survive. And every decision, mechanical or AI, can be explained: you can always ask why the
Filter Parameters kept or dropped a page.

![Screenshot: a Selection funnel (Runs then Selection funnel), the page count dropping from All pages through the filters to the AI pass](screenshots/2-1-funnel-concept.png)

## What a shortlist is for

> "Three uses: get a clean list out, keep it current, and share it so everyone's working from
> the same pages."

People use a shortlist for three things. To hand on a defensible list, downloaded as a spreadsheet
with the columns they need. To keep a view current, re-running it as GOV.UK changes or after they
tweak the definition, and comparing. And to share, so a colleague opens the same shortlist and sees
the same pages and the same reasons, rather than something that lives only in one person's head or
browser history.

![Screenshot: an exported shortlist open in a spreadsheet, showing URL, Title, and the AI keep/drop and reason columns](screenshots/2-2-exported-example.png)

## What you see on the Shortlists home page

> "This home page is just the shelf of saved questions. Open one and you get tabs: the results
> your parameters generated, the runs over them, and the parameters themselves. We'll build one from
> scratch next."

**What you do:** land on **Shortlists**, where Journey 1 left you.

**What you see:** a list of existing shortlists (each with an owner and its filters) and a **Build a
new shortlist** button. Open any shortlist and you get its tabs, which are the shape of everything
that follows:

- **Results** holds the list your parameters generated, with the row-level kept and dropped views
  (Journeys 4 and 6).
- **Runs** is where you run the AI and see every run; its sub-tabs hold the **Selection funnel** and
  **Compare Runs** ([Journey 4: Run the AI](4-run-the-ai.md)).
- **Filter Parameters** is where you set what generates the shortlist, and, after a run, ask the AI
  to sharpen it ([Journey 3: Create a shortlist](3-create-a-shortlist.md)).

![Screenshot: the Shortlists home list, plus a shortlist opened to show its tab row (Results, Runs, GDS Compliance, URL check, Filter Parameters)](screenshots/2-3-shortlist-tabs.png)

## Links

- Previous: [Journey 1: Create an account](1-create-an-account.md)
- Next: [Journey 3: Create a shortlist](3-create-a-shortlist.md)
