"""Server-side guardrails for user-entered text sent to an LLM.

A cheap, deterministic pre-check for the interactive assistants. It blocks personal
contact data (emails, phone numbers) and a configurable list of prohibited terms from
ever reaching the model, and returns a refusal that names the (masked) offending text,
says why, and points to the Acceptable Use Policy. The nuanced judgement — hostility,
hate, prompt-injection — is handled by the assistant's own system-prompt guardrail;
this is the reliable, hard backstop for the parts that can be matched deterministically.

Apply it to USER-entered text only (never to gov.uk page content, which legitimately
contains emails, names, etc.).
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence

# Generic by default ("company policy") since different outfits use this. Override with
# the GUARDRAIL_AUP env var to name/link a specific policy.
AUP_REFERENCE = os.getenv("GUARDRAIL_AUP", "company policy")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Conservative UK-style phone numbers: +44 / 0 then 9–10 more digits (spaces/dashes ok).
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?44\s?\d|0\d)(?:[\s\-]?\d){8,10}(?!\w)")

# Minimal built-in blocklist. Extend per organisation via GUARDRAIL_BLOCKED_TERMS
# (comma-separated) — put the slurs/terms your AUP prohibits there. Matched whole-word,
# case-insensitive; the offending term is never echoed back to the user.
_DEFAULT_BLOCKED = ("fuck", "shit", "cunt")

_PERSONAL_REASON = "category definitions must not contain personal contact details"
_LANGUAGE_REASON = "it contains language company policy does not allow"


def _extra_blocked() -> List[str]:
    raw = os.getenv("GUARDRAIL_BLOCKED_TERMS", "")
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def _mask_email(e: str) -> str:
    name, _, dom = e.partition("@")
    if not (name and dom):
        return "•••"
    return name[:1] + "•••@" + dom[:1] + "•••"


def _mask_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s)
    return "•••" + digits[-2:] if len(digits) >= 2 else "•••"


def check(text: str, extra_terms: Sequence[str] = ()) -> Optional[dict]:
    """Return a finding if `text` should be blocked, else None. A finding is
    {kind, reason, offending}; `offending` is always safe to show (masked / no slur)."""
    if not text:
        return None
    m = _EMAIL_RE.search(text)
    if m:
        return {"kind": "personal_data", "reason": _PERSONAL_REASON,
                "offending": "an email address (" + _mask_email(m.group()) + ")"}
    m = _PHONE_RE.search(text)
    if m:
        return {"kind": "personal_data", "reason": _PERSONAL_REASON,
                "offending": "a phone number (" + _mask_phone(m.group()) + ")"}
    blocked = set(_DEFAULT_BLOCKED) | set(_extra_blocked()) | {t.lower() for t in extra_terms}
    for term in blocked:
        if term and re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE):
            return {"kind": "prohibited_language", "reason": _LANGUAGE_REASON,
                    "offending": "language that is not permitted"}
    return None


def refusal_message(finding: dict) -> str:
    """The user-facing refusal: names the (masked) offending text, why, and the policy,
    and asks them to reword and resend — the assistant then continues from the same point."""
    return ("I can't use that: it contains " + finding["offending"] + " — "
            + finding["reason"] + ". Please remove or reword it and send again, and we'll "
            "carry on from here. See " + AUP_REFERENCE + " for what's acceptable.")
