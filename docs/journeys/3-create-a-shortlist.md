# Journey 3: Create a shortlist

*What this shows: setting the Filter Parameters that generate a shortlist, from picking the owning
organisation down to teaching the AI the tricky judgement calls.*

**Who / when:** a user who knows the topic they want and is ready to define it.
**Pages used:** **Build a new shortlist**, which opens the **Filter Parameters** form.

# Journey Overview

You don't hunt for pages, you describe them, and the tool generates the shortlist. The **Filter
Parameters** form runs from the coarse and certain to the fine and judged: first the mechanical
filters (organisation, page type, keywords), then a few sentences telling the AI what counts as in
or out, and optionally a couple of worked examples for the borderline pages. A few minutes of
describing gives you a re-runnable, explainable list.

---

## How the form is laid out

> "Notice the shape of the form: filters first, because they're cheap and certain, then the AI
> criteria, because that's the judgement. The tool works in exactly that order."

Open **Build a new shortlist**. The **Filter Parameters** form runs top to bottom, in the order the
tool applies things: an **Owner email** and a **Name** for the shortlist (say, "Farm slurry
storage"); then **Pre-Inference Filtering**, the fast, exact narrowing that runs before any AI
(**Organisations**, then **Page Types**, then **Inclusion Keyword(s)**); then **LLM Inference**, what
you tell the AI (what to include, what to exclude, and optional examples of tricky pages). Everything
above the AI section is deterministic, and saving at the bottom generates or rebuilds the shortlist
and sets up a run for you to start.

![Screenshot: the whole Filter Parameters form scrolled to show its order, Owner and Name, Pre-Inference Filtering (Organisations, Page Types, Keywords), then LLM Inference](screenshots/3-0-form-overview.png)

## 3.1: Organisation

> "Start with who owns it. Pick DEFRA, or a specific agency, and if you want the whole family,
> tick 'Include child organisations' to pull in its agencies too. The page counts next to each one
> keep you honest about how much you're letting in."

**What you do:** under **Pre-Inference Filtering**, in **Organisations**, tick the department or body
that owns the pages you want. It's a searchable tree of parents with their agencies underneath, and
each row shows how many pages that organisation has. Tick **Include child organisations** to sweep in
a department's agencies as well as the department itself.

**What you see:** the tree filters as you type, and the counts tell you how big each branch is.

![Screenshot: the Organisations tree, with a search box, a department expanded to its agencies with page counts, and "Include child organisations" ticked](screenshots/3-1-organisations.png)

**How this helps you:** ownership is the most reliable signal on GOV.UK and the biggest single cut.
One tick takes you from all of government to this organisation's pages.

## 3.2: Document types

> "Next, what kind of page. Usually you want guidance and detailed guides, not news stories or
> consultation outcomes. Ticking the page types strips out the formats that aren't the job."

**What you do:** in **Page Types**, tick the kinds of page you want (guidance, detailed guides,
publications, and so on). It's searchable, like Organisations.

**What you see:** the shortlist-to-be narrows again, to just those formats.

![Screenshot: the Page Types picker, a searchable list with "guidance" and "detailed guide" ticked](screenshots/3-2-page-types.png)

**How this helps you:** the same words turn up in very different formats. Filtering by page type
removes whole categories of noise (press releases, transparency data) before the AI ever sees them.

## 3.3: Keywords

> "Finally, the words the page has to actually contain. This is a blunt instrument on purpose,
> the cheap keyword net that catches the obvious candidates. It will over-catch, and that's fine,
> because the AI is about to sort the true matches from the passing mentions."

**What you do:** in **Inclusion Keyword(s)**, list the words a page must contain, one per line or
comma-separated (say, "slurry", "nitrate").

**What you see:** the last mechanical cut. Only pages whose text contains your keywords survive to
the AI stage.

![Screenshot: the Inclusion Keyword(s) box with "slurry" and "nitrate" entered](screenshots/3-3-keywords.png)

**How this helps you:** keywords are fast but literal, so they can't tell slurry the manure from
slurry the concrete. You want them to over-include here, because the next section is where the
judgement happens.

## 3.4: Prompts (include, exclude, and the coin-flips)

This is where you stop filtering and start judging. Under **LLM Inference**, the AI reads each page
that survived the filters and decides whether it truly belongs, and it can only be as good as the
guidance you give it here.

### What pages do you want to include?

> "Describe what you actually mean, not keywords but meaning. 'Pages about storing and spreading
> livestock slurry and the rules around it.' That sentence is the ruler the AI measures every page
> with."

**What you do:** in a few sentences, describe what genuinely belongs, the scope in your own words.

**What you see:** this becomes the AI's inclusion criteria, the yardstick it scores each page against
(0 to 1, keeping anything above zero).

![Screenshot: the "What pages do you want to include?" box with a plain-English scope sentence typed in](screenshots/3-4a-include.png)

### What pages do you want to exclude from here?

> "Then the traps. 'Slurry can mean coal or concrete, drop those. Drop sewage sludge and
> biosolids.' This is a second, stricter read whose only job is to take out the impostors the first
> pass let through."

**What you do:** describe the look-alikes that should be dropped even though they passed the keyword
net.

**What you see:** this becomes the exclusion criteria, a second AI pass that re-checks the kept pages
and can only remove.

![Screenshot: the "What pages do you want to exclude from here?" box with the look-alike traps typed in](screenshots/3-4b-exclude.png)

### Examples of tricky pages (optional)

> "For the genuinely borderline pages, don't argue with the AI in the abstract, show it. A
> couple of 'keep this even though...' and 'drop this because...' examples turn a coin-flip into a
> rule. It's the single biggest thing you can do to make runs repeatable."

Some pages sit right on the line: a grant page that funds slurry stores but is mostly about payments,
or a page that mentions slurry once in passing. On a borderline page the AI is essentially flipping a
coin, and two runs can disagree.

**What you do:** in **Examples of tricky pages**, give the AI a couple of worked examples. A Keep
example such as "a grant page that funds slurry stores even if the rest is about payments", and a
Drop example such as "sewage sludge or biosolids, not animal slurry".

**What you see:** these examples steer the borderline calls, so the coin lands the same way each time
and the shortlist stops wobbling between runs.

![Screenshot: the "Examples of tricky pages" section, with the Keep and Drop boxes holding one example each](screenshots/3-4c-tricky-keep-drop.png)

**How this helps you:** you don't need examples if your include and exclude criteria are already
crisp, but when the same page keeps flipping between runs, a Keep or Drop example is the precise fix.
And you don't have to invent them alone. Once you've done at least one run, this same **Filter
Parameters** page grows a **"Do you want to try to improve your prompt with AI?"** card at the top: it
reviews your runs and proposes sharper criteria and examples, each with an **Apply to field** button
that drops the wording straight into the box for you to edit and save. Treat it as inspiration rather
than gospel, as the card itself says. [Journey 7: Edit your Filter Parameters](7-edit-filter-parameters.md)
covers it.

## Save, and the shortlist is generated

> "Save, and the tool builds the shortlist there and then and sets up a run for it. It drops you on
> that run's details page, so the next step — starting the AI over your pages — is one click away."

**What you do:** press **Save** at the bottom of the form.

**What you see:** the tool builds the shortlist from your parameters and creates a fresh run over it,
then takes you to that run's **details page**, ready to press **Start Run**. Starting that pass is
[Journey 4: Run the AI](4-run-the-ai.md); getting the list out is
[Journey 5: Download a shortlist](5-download-a-shortlist.md); checking how it was built is
[Journey 6: How did we build the shortlist?](6-how-was-this-built.md).

![Screenshot: the Save button at the foot of the Filter Parameters form, then the fresh run's details page it lands on, with the Start Run button](screenshots/3-5-save-generated.png)

## Links

- Previous: [Journey 2: What is a shortlist?](2-what-is-a-shortlist.md)
- Next: [Journey 4: Run the AI](4-run-the-ai.md)
- See it work: [Journey 6: How did we build the shortlist?](6-how-was-this-built.md)
