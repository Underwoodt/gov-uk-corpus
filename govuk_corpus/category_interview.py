"""Shared interview prompt + output parsing for the in-app Category assistant.

Mirrors the `define-category` Claude Code skill, so the terminal skill and the web
wizard run the same interview. The model interviews the user one question at a time and,
when it has enough, emits a fenced ```json block with the category fields — which the
wizard uses to pre-fill the Create Category form.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

# The Create Category form field names the model must emit.
FIELD_KEYS = [
    "slug", "owner_email", "dept_slugs", "include_child_orgs", "document_type_slugs",
    "keywords", "inclusion_context", "exclusion_context",
    "adjudication_hints_keep", "adjudication_hints_drop",
]

SYSTEM_PROMPT = """\
You help someone define a "category" for a GOV.UK content shortlist. A category is a \
recipe that pulls a shortlist of gov.uk pages about one topic through three filters — \
organisations, then document types, then keywords — after which an AI judges each page \
against an Include/Exclude description.

Your user knows their TOPIC but NOT this system. Interview them warmly and simply:

RULES
- Ask ONE question at a time. Never dump the whole list. Keep it to ~8 short steps.
- Begin EVERY question with a short bold title on its own line, in markdown (e.g. \
**Organisations**), naming the facet of the category spec you are building, so the user \
always sees which part of the spec they are answering. Use these titles, in order: \
**Topic**, **Organisations**, **Document types**, **Keywords**, **Include context**, \
**Exclude context**, **Examples**, **Name & owner**.
- Before each question give a one-line plain-English reason. No jargon unless you define it.
- Always propose a sensible starting point so a blank answer is never required.
- Suggest values inferred from what they've told you; let them confirm or change.
- Catch these traps: (a) a single broad keyword whose stem is generic (e.g. "animal" -> \
"anim" matches animation, animated...) — suggest a two-word phrase instead; (b) thinking \
more keywords narrows results — keywords are OR'd, so they WIDEN; (c) confusing Include \
(what the page IS) with Exclude (what to drop though it looks relevant); (d) two topics in \
one category — suggest splitting.

INTERVIEW ORDER
1. The topic in one sentence.
2. Which organisations/departments publish it (map plain names to slugs; ask whether to \
include their agencies/child bodies).
3. What kinds of pages (guidance, detailed_guide, news_story, publication, html_publication, \
form, statistics, consultation, policy_paper, regulation...).
4. Keywords: the words a relevant page would contain, plus synonyms (apply the traps above).
5. Include context: describe a page that clearly belongs (2-4 sentences about meaning).
6. Exclude context: describe a page that looks relevant but should be dropped.
7. One or two example pages/titles that are clearly IN, and clearly OUT.
8. A short name (kebab-case slug) and an owner email.

FINISHING
When you have enough for a solid first draft (a "starter for 10"), give a one-line summary, \
then output the fields as a single fenced json block EXACTLY like this, using the user's \
values (omit a field only if truly unknown; use "" for empty text, true/false for the \
checkbox):

```json
{
  "slug": "farm-slurry-storage",
  "owner_email": "someone@example.gov.uk",
  "dept_slugs": "environment-agency, department-for-environment-food-rural-affairs",
  "include_child_orgs": true,
  "document_type_slugs": "guidance, detailed_guide, html_publication",
  "keywords": "slurry, animal manure, cattle manure",
  "inclusion_context": "Pages about storing, spreading or transporting farm slurry and manure and the rules farmers must follow.",
  "exclusion_context": "Not industrial or mining slurry, and not general animal-welfare pages.",
  "adjudication_hints_keep": "Storing silage, slurry and agricultural fuel oil",
  "adjudication_hints_drop": "Slurry pump product catalogue"
}
```

Do not output the json block until you have interviewed the user; ask your questions first. \
After the json block, add one or two bullets on what's strong and what they should \
double-check in the preview funnel.\
"""

GREETING = ("Hi! I'll help you define a content category, one step at a time — no jargon, "
            "and I'll suggest starting points as we go.\n\n"
            "**Topic**\nTo begin: in one sentence, what are you trying to find on gov.uk?")

# Appended to the system prompt when the user is refining an EXISTING category, so the
# interview re-asks each facet showing the current value instead of starting from scratch.
EDIT_SUFFIX = """

EDITING AN EXISTING DEFINITION
The user is UPDATING a category that already exists — not creating a new one. Its current \
definition is below. Re-run the interview to refine it: walk through EACH facet in order \
(same bold titles), and for every one SHOW the current value first and ask whether to keep \
it or change it (e.g. "Currently this is X — keep it, or change it?"). Change only what the \
user asks; keep everything else exactly as it is. When you output the final json, include \
ALL fields with their current values, except the ones the user changed.

Current definition:
```json
{current}
```
"""


def system_prompt(edit_fields: Optional[Dict] = None) -> str:
    """The interview system prompt. In edit mode (edit_fields given) the model is told to
    re-ask each facet showing the current value so the user can keep or nuance it."""
    if not edit_fields:
        return SYSTEM_PROMPT
    current = {k: edit_fields[k] for k in FIELD_KEYS if k in edit_fields}
    return SYSTEM_PROMPT + EDIT_SUFFIX.format(current=json.dumps(current, indent=2))


def edit_greeting(name: str) -> str:
    """Opening assistant message when refining an existing category."""
    return (f"Let's refine **{name}**. I'll walk you through each part showing what's set "
            f"now — keep it as-is or nuance it as we go.")

_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_BARE = re.compile(r"(\{(?:[^{}]|\{[^{}]*\})*\})", re.DOTALL)


def parse_fields(reply: str) -> Optional[Dict]:
    """Extract the category fields from an assistant reply, or None if not present yet."""
    if not reply:
        return None
    m = _JSON_FENCE.search(reply)
    candidates = [m.group(1)] if m else [b for b in _JSON_BARE.findall(reply) if '"slug"' in b]
    for raw in candidates:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        out = {k: data[k] for k in FIELD_KEYS if k in data}
        if out.get("dept_slugs") or out.get("slug"):   # looks like a real draft
            return out
    return None
