"""URL canonicalisation for the GOV.UK corpus.

One rule, applied on EVERY insert and EVERY join. This is the single biggest
data-quality lever: the `test-scopes-3` join went from 170/194 to 190/194 purely
by normalising the host (`gov.uk` -> `www.gov.uk`), and one row there was a
malformed concatenation (`https://gov.ukhttps://webarchive...`) that must be
rejected on ingest.

Canonical form for a GOV.UK page URL:
  - scheme forced to https
  - host lowercased; bare `gov.uk` promoted to `www.gov.uk`
  - fragment (#...) and query (?...) dropped (GOV.UK content pages are path-keyed)
  - trailing slash removed, except the site root
  - path left case-sensitive (GOV.UK paths are case-sensitive)

`canonicalise` returns None for anything that is not a usable GOV.UK URL, so
callers can record it as invalid rather than poisoning the corpus.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, urlunsplit

GOVUK_HOST = "www.gov.uk"
_ALLOWED_HOSTS = {"www.gov.uk", "gov.uk"}


def canonicalise(raw: Optional[str]) -> Optional[str]:
    """Return the canonical GOV.UK URL, or None if the input is unusable."""
    if not raw:
        return None
    url = raw.strip()
    if not url:
        return None

    # Must be a single http(s) URL. A second scheme anywhere after the first
    # means two URLs were concatenated (e.g. the webarchive bug) -> reject.
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")):
        return None
    body = lowered[lowered.index("://") + 3:]
    if "://" in body:
        return None

    parts = urlsplit(url)
    host = parts.netloc.lower()
    # Strip any userinfo/port noise; we only accept the GOV.UK web host.
    host = host.split("@")[-1].split(":")[0]
    if host not in _ALLOWED_HOSTS:
        return None
    host = GOVUK_HOST  # promote bare gov.uk -> www.gov.uk

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    # Drop query and fragment for page identity.
    return urlunsplit(("https", host, path, "", ""))


def is_valid(raw: Optional[str]) -> bool:
    """True if `raw` canonicalises to a usable GOV.UK URL."""
    return canonicalise(raw) is not None


def path_of(url: str) -> Optional[str]:
    """The path portion of a canonical URL, for building the /api/content call."""
    c = canonicalise(url)
    if c is None:
        return None
    return urlsplit(c).path
