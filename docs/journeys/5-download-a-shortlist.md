# Journey 5: Download a shortlist

*What this shows: getting the shortlist out of the tool as a file, choosing the columns you need and
the format that suits where it's going next.*

**Who / when:** anyone who needs to hand the list on, analyse it in a spreadsheet, or feed it to
another system.
**Pages used:** the **Results** tab, then **Download**.

# Journey Overview

A shortlist isn't much use trapped in a browser. From the **Results** tab, **Download** gives you a
file: pick which run to export, tick the columns you need (URL always comes along), and choose CSV,
Excel or JSON. Because every column carries its provenance, the file is as defensible as the
shortlist behind it.

---

## Getting to the download

**What you do:** open the **Results** tab for your shortlist and choose **Download**.

**What you see:** a download page with three choices: which run to export, which fields to include,
and what format to produce. It opens with sensible defaults, so you can download straight away or
fine-tune first.

> Say: "From the Results tab, hit Download. You get one screen: which version to export, which
> columns, and which file type. The defaults are already chosen, so tweak only what you care about."

![Screenshot: the Download page, with the Run selector, the field picker, and the Output type dropdown](screenshots/5-0-download-page.png)

**A note on which run:** if you've run the AI more than once, a **Run** selector lets you export the
list as a specific run judged it, so the file matches exactly the version you're talking about.

## 5.1: The fields you can export

**What you do:** tick the columns you want from a grouped picker. URL is always included, since it's
the one column every downstream use needs, so it's locked on. The rest are grouped so you can grab a
whole theme at once:

- **Content:** URL, Title, and heavier optional fields like the parent document type, the full body
  text, or the raw page JSON. The last two can make the file very large, and the picker warns you.
- **Provenance:** where the page came into the shortlist from.
- **Popularity:** the GOV.UK Search view count (roughly the last two weeks), and whether that count
  is the page's own or inherited from a parent publication.
- **Matching and AI:** the AI's verdict and its reasoning, so kept or dropped, the score, the reason,
  the topic it found, and where in the page the evidence was.
- **Ownership:** the organisation or organisations responsible for the page.
- **Quality:** readability and similar signals.
- **Freshness:** when the page was last updated.

**What you see:** only the ticked columns appear in the file, in a tidy order.

> Say: "Think about who's reading the file. A policy colleague might want title, URL and the AI's
> reason. An analyst might add view counts and freshness. Someone loading it into another system
> might take the raw JSON. Tick the groups you need, and URL always comes along."

![Screenshot: the grouped field picker (Content, Provenance, Popularity, Matching and AI, Ownership, Quality, Freshness), with URL ticked and locked on](screenshots/5-1-field-picker.png)

**How this helps you:** the groups mirror the questions people ask of a shortlist. What is it, where
did it come from, is it read, did the AI keep it and why, who owns it, is it any good, and is it
current. So you grab a theme rather than hunting field by field.

For the exact columns in each group, and what each one holds, expand the reference below.

<details>
<summary>Full list of exportable fields</summary>

### Content

| Field name | What it shows |
|---|---|
| URL | The page's canonical GOV.UK URL. Always included. |
| Title | The page title. |
| Parent document type | For `html_publication` pages, the parent publication's type. |
| Body text (search_text) | The full page text. Can make the download very large. |
| Content (raw JSON) | The raw page JSON. Can make the download very large. |

### Provenance

| Field name | What it shows |
|---|---|
| Source / provenance | How the page entered the shortlist: corpus keyword, GOV.UK Search, or both. |

### Popularity

| Field name | What it shows |
|---|---|
| View count | GOV.UK Search pageviews (about the last 14 days). For attachment or guide-part sub-pages this is the parent publication's count. |
| View count source | `page` = the page's own count; `parent` = inherited from the parent publication. |
| View count collected | The date the view count was collected. |

### Matching & AI decision

| Field name | What it shows |
|---|---|
| Run ID | The inclusion run the AI columns come from. Always included, so rows from several runs can be told apart in one file. |
| Organisations | All linked organisation slugs. |
| Document type | The page's document type. |
| Matched keywords | The shortlist keywords this page matched. |
| Inclusion reason | From the latest AI run; blank for pages not evaluated. |
| Exclusion reason | From the latest AI run's exclusion pass. |
| Inclusion raw reply | The model's verbatim inclusion reply. Can be long. |
| Exclusion raw reply | The model's verbatim exclusion reply. Can be long. |
| Stage | The funnel stage or phase these rows are shown at. |
| Stage decision | The AI verdict for the page: Keep, Drop, or Unscored, from the run relevant to the stage. |
| Furthest stage reached | How far the page got in the funnel: Keyword search, Reached LLM inclusion, Reached exclusion, or Final shortlist. |
| Confidence | How central the topic is, from the inclusion score: Wrong sense, Mentioned in passing, Discussed a moderate amount, or Major focus. |
| Primary topic | From the latest inclusion run: what the page is mainly about, a short noun phrase grounded in its title and description. |
| Matched in | From the latest inclusion run: which fields carried the topic (title, description, body). |
| Evidence quotes | From the latest inclusion run: the verbatim passages quoted as grounding for the decision. |

### Ownership

| Field name | What it shows |
|---|---|
| Primary publishing organisation | The main organisation responsible for the page. |

### Quality attributes

| Field name | What it shows |
|---|---|
| Size | The page size. |
| Readability score | The reading-age / readability signal for the page text. |
| GDS number of issues | How many GDS plain-English issues were found. |
| GDS issues text | The GDS issues themselves. |
| GDS stars | A star rating from the GDS check. |

### Freshness

| Field name | What it shows |
|---|---|
| First published at | When the page was first published. |
| Public updated at | When the page was last updated on GOV.UK. |
| Last seen (crawl) | When our corpus last saw the page. |

</details>

## 5.2: Formats

**What you do:** pick an **Output type**:

- **CSV (.csv):** opens anywhere and loads into anything. The safe default for sharing.
- **Excel (.xlsx):** a real spreadsheet with formatting, best when a person will read and sort it.
- **JSON (.json):** structured data, best when another system or script will consume it.

The file extension is added for you.

**What you see:** the file downloads with the columns you picked, named after the shortlist.

> Say: "CSV if in doubt, since it opens anywhere. Excel if a person's going to live in the
> spreadsheet. JSON if a machine's going to read it. Same data, three shapes."

![Screenshot: the Output type dropdown open, showing CSV (.csv), Excel (.xlsx) and JSON (.json)](screenshots/5-2-formats.png)

## Links

- Previous: [Journey 4: Run the AI](4-run-the-ai.md)
- Next: [Journey 6: How was this built?](6-how-was-this-built.md)
