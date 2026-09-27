# Journey 4 — Download a shortlist

*What this shows: getting the shortlist out of the tool as a file — choosing the columns you need
and the format that suits where it's going next.*

**Who / when:** anyone who needs to hand the list on, analyse it in a spreadsheet, or feed it to
another system.
**Pages used:** the **Shortlist** tab → **Download**.

---

## The 30-second pitch

> Say: "A shortlist isn't much use trapped in a browser. One click gets you a file — pick exactly
> the columns you want, choose a spreadsheet or a data format, and it's ready to send, analyse, or
> load. And because every column comes with its provenance, the file is as defensible as the
> shortlist behind it."

---

## Getting to the download

**What you do:** open the **Shortlist** tab for your shortlist and choose **Download**.

**What you see:** a download page with three choices — *which run* to export, *which fields* to
include, and *what format* to produce. It opens pre-set to sensible defaults, so you can just hit
download, or fine-tune first.

> Say: "From the Shortlist tab, hit Download. You get one screen: which version to export, which
> columns, and which file type. Defaults are already chosen — tweak only what you care about."

![Screenshot — the Download page: the Run selector, the field picker, and the Output type dropdown](screenshots/4-0-download-page.png)

**Note — which run:** if you've run the AI more than once, a **Run** selector lets you export the
list as a specific run judged it, so the file matches exactly the version you're talking about.

---

## 4.1 — The fields you can export

**What you do:** tick the columns you want from a grouped picker. **URL is always included** (it's
the one column every downstream use needs, so it's locked on). The rest are grouped so you can grab
a whole theme at once:

- **Content** — URL, Title, and heavier optional fields like the parent document type, the full
  **body text**, or the raw page JSON *(the last two can make the file very large — the picker
  warns you)*.
- **Provenance** — where the page came into the shortlist from (its source).
- **Popularity** — GOV.UK Search **view count** (roughly the last two weeks) and whether that count
  is the page's own or inherited from a parent publication.
- **Matching & AI** — the AI's verdict and its reasoning: kept/dropped, score, the reason, the
  topic it found, and where in the page the evidence was.
- **Ownership** — the organisation(s) responsible for the page.
- **Quality** — readability and similar signals.
- **Freshness** — when the page was last updated.

**What you see:** only the ticked columns appear in the file, in a tidy order.

> Say: "Think about who's reading the file. A policy colleague might want title, URL and the AI's
> reason. An analyst might add view counts and freshness. Someone loading it into another system
> might take the raw JSON. Tick the groups you need — URL always comes along."

![Screenshot — the grouped field picker with the groups (Content, Provenance, Popularity, Matching & AI, Ownership, Quality, Freshness); URL ticked and locked on](screenshots/4-1-field-picker.png)

**Why the grouping:** the columns mirror the questions people ask of a shortlist — *what is it,
where did it come from, is it read, did the AI keep it and why, who owns it, is it any good, is it
current.* Grab a theme rather than hunting field by field.

---

## 4.2 — Formats

**What you do:** pick an **Output type**:

- **CSV (.csv)** — universal; opens anywhere, loads into anything. The safe default for sharing.
- **Excel (.xlsx)** — a real spreadsheet with formatting; best when a person will read and sort it.
- **JSON (.json)** — structured data; best when another system or script will consume it.

The file extension is added for you.

**What you see:** the file downloads with the columns you picked, named after the shortlist.

> Say: "CSV if in doubt — it opens anywhere. Excel if a person's going to live in the spreadsheet.
> JSON if a machine's going to read it. Same data, three shapes."

![Screenshot — the Output type dropdown open, showing CSV (.csv), Excel (.xlsx) and JSON (.json)](screenshots/4-2-formats.png)

---

## In one breath

> Say: "From the Shortlist tab, hit Download, pick the run, tick the column groups you need — URL's
> always there — choose CSV, Excel or JSON, and you've got a defensible file ready to hand on."

## Links

- Previous: [Journey 3 — Create a shortlist](3-create-a-shortlist.md)
- Next: [Journey 5 — How was this built? Is it what I want?](5-how-was-this-built.md)
