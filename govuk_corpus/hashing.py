"""Content hashing for change detection.

The hash is of the raw /api/content JSON text. `lastmod` from the sitemap is the
cheap prefilter that decides whether to fetch; this hash is the exact confirmation
that the fetched content actually changed before we write a new version.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Optional


def content_hash(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return sha256(text.encode("utf-8")).hexdigest()
