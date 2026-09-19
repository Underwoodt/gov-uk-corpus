"""HTML → plain text, and body extraction from an /api/content payload.

Used to build `search_text` (the body content, tags stripped) for keyword search.
Pure stdlib — no external dependencies.
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import Any, Dict

_SKIP = {"script", "style"}
_BREAK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "br", "div"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if tag in _BREAK:
            self.chunks.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


def html_to_text(raw: str) -> str:
    """Strip tags/entities from an HTML fragment, returning collapsed plain text."""
    if not raw:
        return ""
    parser = _TextExtractor()
    parser.feed(_html.unescape(raw))
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.chunks)).strip()


def body_text(payload: Dict[str, Any]) -> str:
    """Readable body text from an /api/content payload: details.body + details.parts[].body."""
    details = payload.get("details") or {}
    pieces: list[str] = []
    if details.get("body"):
        pieces.append(html_to_text(details["body"]))
    for part in details.get("parts") or []:
        if part.get("body"):
            pieces.append(html_to_text(part["body"]))
    return " ".join(p for p in pieces if p)


# Placeholder that a scrubbed organisation name/slug is replaced with. Chosen so full-text
# search can't re-derive a keyword from it: its lexemes are replac/org/slug (no department
# words like food/rural/environment). Kept as a marker so the scrub is visible in search_text.
ORG_MARKER = "replacement-org-slug"


def _slug_of(base_path: str) -> str:
    """Last path segment of an organisation's base_path (its gov.uk slug)."""
    return (base_path or "").rstrip("/").rsplit("/", 1)[-1]


def organisation_names(payload: Dict[str, Any]) -> list:
    """Titles of the page's own organisations (primary + related), from the payload links."""
    links = payload.get("links") or {}
    names: list[str] = []
    for key in ("primary_publishing_organisation", "organisations"):
        for it in links.get(key) or []:
            title = (it.get("title") or "").strip()
            if title:
                names.append(title)
    return names


def organisation_refs(payload: Dict[str, Any]) -> list:
    """Both the title AND the slug of each of the page's own organisations — the strings to
    scrub from the searchable text, so a keyword doesn't match a page merely because that
    organisation published it (whether the name or the slug appears)."""
    links = payload.get("links") or {}
    refs: list[str] = []
    for key in ("primary_publishing_organisation", "organisations"):
        for it in links.get(key) or []:
            title = (it.get("title") or "").strip()
            if title:
                refs.append(title)
            slug = _slug_of(it.get("base_path") or "")
            if slug:
                refs.append(slug)
    return refs


def strip_phrases(text: str, phrases, replacement: str = " ") -> str:
    """Replace each phrase (case-insensitive, whole-string occurrences) in `text` with
    `replacement`, then collapse whitespace. Longest phrases first so a longer name is handled
    before a shorter name nested inside it."""
    if not text:
        return text
    for p in sorted({p for p in phrases if p}, key=len, reverse=True):
        text = re.sub(re.escape(p), replacement, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def search_text(payload: Dict[str, Any]) -> str:
    """The body text used for keyword search, with the page's own organisation names and slugs
    replaced by ORG_MARKER (so e.g. a Defra page doesn't match 'food' just because 'Department
    for Environment, Food & Rural Affairs' — or its slug — appears in its text). This is what
    `content.search_text` stores."""
    return strip_phrases(body_text(payload), organisation_refs(payload),
                         replacement=f" {ORG_MARKER} ")
