"""Render the docs/journeys/*.md user guides as in-app Help pages — from the same Markdown files
the team maintains, so Help never drifts from the docs (single source of truth).

Deliberately small: the journeys use only headings, paragraphs, bullet/ordered lists, inline
emphasis/code/links, images, blockquotes and horizontal rules — no tables or fenced code — so a
dependency-free renderer covers them (keeps the deploy a git-pull + restart, no pip step).

The *help view* also drops the presenter-only parts that make sense in a demo script but not in
on-screen help — the ``> Say:`` narration and the "30-second pitch" section — and renders the
(not-yet-served) screenshot placeholders as captions rather than broken images.
"""
from __future__ import annotations

import html
import os
import re
from typing import Dict, List, Optional, Tuple

_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "docs", "journeys"))
# A valid journey slug is a filename stem like "4-run-the-ai": digits, then lowercase/hyphens.
# The pattern alone rules out path traversal (no dots or slashes).
_SLUG_RE = re.compile(r"^[0-9]+-[a-z0-9-]+$")


def _files() -> List[str]:
    try:
        return sorted(f for f in os.listdir(_DIR)
                      if f.endswith(".md") and f != "README.md" and _SLUG_RE.match(f[:-3]))
    except OSError:
        return []


def _num_key(slug: str) -> tuple:
    m = re.match(r"^(\d+)", slug)
    return (int(m.group(1)) if m else 999, slug)


def _read(slug: str) -> Optional[str]:
    if not _SLUG_RE.match(slug) or f"{slug}.md" not in _files():
        return None
    try:
        with open(os.path.join(_DIR, f"{slug}.md"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _title_and_blurb(text: str) -> Tuple[str, str]:
    """The H1 (page title) and the italic *What this shows…* strapline under it."""
    title, blurb = "", ""
    for line in text.splitlines():
        s = line.strip()
        if not title and s.startswith("# "):
            title = s[2:].strip()
            continue
        if title and not blurb and len(s) > 2 and s.startswith("*") and s.endswith("*"):
            blurb = s.strip("*").strip()
            if blurb.lower().startswith("what this shows:"):
                blurb = blurb[len("what this shows:"):].strip()
            break
    return title, blurb


def list_journeys() -> List[Dict]:
    """All journeys in order: [{slug, title, blurb}]."""
    out = []
    for f in _files():
        slug = f[:-3]
        title, blurb = _title_and_blurb(_read(slug) or "")
        out.append({"slug": slug, "title": title or slug, "blurb": blurb})
    out.sort(key=lambda d: _num_key(d["slug"]))
    return out


# ---- rendering -----------------------------------------------------------

def _heading_id(text: str) -> str:
    """GitHub-style anchor id so in-doc links like #64--letting-… resolve."""
    t = re.sub(r"[^\w\s-]", "", text.strip().lower())   # drop punctuation (incl. em dash)
    return t.replace(" ", "-")                            # keep repeated hyphens (GitHub does)


def _rewrite_url(url: str, cur_slug: str) -> str:
    if url.startswith(("http://", "https://", "mailto:", "#")):
        return url
    m = re.match(r"^([0-9][a-z0-9-]*)\.md(#.*)?$", url)   # another journey → its Help page
    if m:
        return f"/help/{m.group(1)}{m.group(2) or ''}"
    return url


SCREENSHOTS_DIR = os.path.join(_DIR, "screenshots")
_SHOT_NAME = re.compile(r"^[A-Za-z0-9._-]+\.(?:png|jpe?g|gif|webp|svg)$", re.IGNORECASE)


def screenshot_path(name: str) -> Optional[str]:
    """Absolute path of a served screenshot, or None if the name is unsafe or the file is
    missing. Used both to render the <img> and (by the web route) to serve the bytes."""
    if not _SHOT_NAME.match(name or ""):
        return None
    p = os.path.normpath(os.path.join(SCREENSHOTS_DIR, name))
    if os.path.dirname(p) != os.path.normpath(SCREENSHOTS_DIR) or not os.path.isfile(p):
        return None
    return p


def _image_html(alt: str, src: str) -> str:
    """A real <img> when the referenced screenshot exists on disk, else a caption placeholder
    (the images may not have been captured yet)."""
    alt_e = html.escape(alt)
    m = re.match(r"^screenshots/([^/]+)$", (src or "").strip())
    if m and screenshot_path(m.group(1)):
        url = "/help/screenshots/" + m.group(1)
        return (f'<figure class="help-shot"><img src="{url}" alt="{alt_e}" loading="lazy">'
                f'<figcaption>{alt_e}</figcaption></figure>')
    return f'<p class="help-figure">🖼 <em>{alt_e}</em></p>'


def _inline(s: str, cur_slug: str) -> str:
    codes: List[str] = []
    s = re.sub(r"`([^`]+)`", lambda m: codes.append(m.group(1)) or f"\x00{len(codes)-1}\x00", s)
    s = html.escape(s, quote=False)   # links/emphasis markers survive (only & < > are escaped)

    def _link(m):
        txt, url = m.group(1), _rewrite_url(m.group(2), cur_slug)
        ext = ' target="_blank" rel="noopener"' if url.startswith("http") else ""
        return f'<a href="{url}"{ext}>{txt}</a>'

    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", s)
    return s


def _strip_presenter(text: str) -> str:
    """Remove the '30-second pitch' section and every ``> Say:`` narration block."""
    lines, out, i, n = text.splitlines(), [], 0, len(text.splitlines())
    while i < n:
        s = lines[i].strip()
        if s.lower().startswith("## the 30-second pitch"):
            i += 1
            while i < n and lines[i].strip() != "---":
                i += 1
            i += 1   # skip the closing rule too
            continue
        if s.lower() == "## in one breath":   # its only content is a Say: summary → drop the heading too
            i += 1
            continue
        if s.startswith(">") and s[1:].strip().lower().startswith("say:"):
            while i < n and lines[i].strip().startswith(">"):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


_BLOCK_START = re.compile(r"^(#{1,6}\s|[-*]\s|\d+\.\s|>|!\[|-{3,}$|\*{3,}$)")


def _md_to_html(text: str, cur_slug: str) -> str:
    lines = _strip_presenter(text).splitlines()
    parts, i, n = [], 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,})$", s):
            parts.append("<hr>"); i += 1; continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            parts.append(f'<h{lvl} id="{_heading_id(m.group(2))}">{_inline(m.group(2).strip(), cur_slug)}</h{lvl}>')
            i += 1; continue
        im = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", s)
        if im:
            parts.append(_image_html(im.group(1), im.group(2)))
            i += 1; continue
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip()); i += 1
            parts.append(f'<blockquote class="help-note">{_inline(" ".join(buf).strip(), cur_slug)}</blockquote>')
            continue
        if re.match(r"^[-*]\s+", s):
            parts.append("<ul>")
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                item = re.sub(r"^[-*]\s+", "", lines[i].strip()); i += 1
                while i < n and lines[i].strip() and not _BLOCK_START.match(lines[i].strip()):
                    item += " " + lines[i].strip(); i += 1   # lazy continuation (wrapped text)
                parts.append("<li>" + _inline(item, cur_slug) + "</li>")
            parts.append("</ul>"); continue
        if re.match(r"^\d+\.\s+", s):
            parts.append("<ol>")
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                item = re.sub(r"^\d+\.\s+", "", lines[i].strip()); i += 1
                while i < n and lines[i].strip() and not _BLOCK_START.match(lines[i].strip()):
                    item += " " + lines[i].strip(); i += 1
                parts.append("<li>" + _inline(item, cur_slug) + "</li>")
            parts.append("</ol>"); continue
        buf = [s]; i += 1
        while i < n and lines[i].strip() and not _BLOCK_START.match(lines[i].strip()):
            buf.append(lines[i].strip()); i += 1
        parts.append(f"<p>{_inline(' '.join(buf), cur_slug)}</p>")
    return "\n".join(parts)


def render(slug: str) -> Optional[Tuple[str, str]]:
    """(title, body_html) for a journey, or None if the slug is unknown/invalid.
    The H1 is returned as the title and dropped from the body (the page shows it as the heading)."""
    text = _read(slug)
    if text is None:
        return None
    title, _ = _title_and_blurb(text)
    body = re.sub(r"^\s*#\s+.*\n", "", text, count=1)   # remove the first H1
    return (title or slug, _md_to_html(body, slug))
