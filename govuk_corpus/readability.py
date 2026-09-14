"""Readability + GDS plain-English analysis of body text (stdlib only).

- reading_age(text): UK reading age = Flesch-Kincaid Grade Level + 5.
- gds_issue_count(text): count of GOV.UK style-guide red flags — discouraged
  words/phrases ("words to avoid") plus over-long sentences. Lower is better,
  0 is clean.

Deterministic and dependency-free, so it is unit-testable and safe to run over
the whole corpus.
"""
from __future__ import annotations

import re
from typing import Optional

_WORD = re.compile(r"[A-Za-z]+")
_SENT_SPLIT = re.compile(r"[.!?]+")
_LONG_SENTENCE_WORDS = 25   # GDS: keep sentences short


def _count_syllables(word: str) -> int:
    """Heuristic syllable count (vowel groups, silent trailing 'e'). Min 1."""
    word = word.lower()
    count, prev_vowel = 0, False
    for ch in word:
        is_vowel = ch in "aeiouy"
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def flesch_kincaid_grade(text: str) -> Optional[float]:
    """US school grade level. None when there is too little text to be meaningful."""
    words = _WORD.findall(text or "")
    if len(words) < 10:
        return None
    sentences = max(1, len(_SENT_SPLIT.findall(text)))
    syllables = sum(_count_syllables(w) for w in words)
    grade = 0.39 * (len(words) / sentences) + 11.8 * (syllables / len(words)) - 15.59
    return round(grade, 1)


def reading_age(text: str) -> Optional[float]:
    """UK reading age in years (Flesch-Kincaid grade + 5). None for too-short text."""
    grade = flesch_kincaid_grade(text)
    if grade is None:
        return None
    return round(max(5.0, grade + 5.0), 1)


# GOV.UK style guide "words to avoid" (a practical subset) + phrases.
_AVOID_WORDS = {
    "advancing", "collaborate", "combating", "commit", "countering", "deploy",
    "dialogue", "disincentivise", "empower", "facilitate", "foster", "impactful",
    "initiate", "leverage", "liaise", "overarching", "pledge", "robust",
    "streamline", "strengthening", "tackling", "transform", "utilise", "utilize",
    "utilising", "utilizing", "endeavour", "commence", "purchase", "additional",
    "regarding", "aforementioned", "henceforth", "hereby", "notwithstanding",
}
_AVOID_PHRASES = [
    "in order to", "going forward", "at this moment in time", "please note",
    "with regard to", "in respect of", "prior to", "in the event that",
    "a number of", "ring fenced", "ring-fenced", "best practice", "deep dive",
]


def _gds_scan(text: str):
    """Single scan -> (issue_count, findings_text). Findings is a readable summary
    of what was flagged, so the count and the text always agree."""
    if not text:
        return 0, ""
    low = text.lower()
    word_hits = {}
    for w in _AVOID_WORDS:
        n = len(re.findall(r"\b" + re.escape(w) + r"\b", low))
        if n:
            word_hits[w] = n
    phrase_hits = {}
    for p in _AVOID_PHRASES:
        n = low.count(p)
        if n:
            phrase_hits[p] = n
    long_sentences = sum(1 for s in _SENT_SPLIT.split(text)
                         if len(_WORD.findall(s)) > _LONG_SENTENCE_WORDS)

    count = sum(word_hits.values()) + sum(phrase_hits.values()) + long_sentences
    parts = []
    if word_hits:
        parts.append("Words to avoid: "
                     + ", ".join(f"{w} ({n})" for w, n in sorted(word_hits.items())))
    if phrase_hits:
        parts.append("Phrases to avoid: "
                     + ", ".join(f"{p} ({n})" for p, n in sorted(phrase_hits.items())))
    if long_sentences:
        parts.append(f"Over-long sentences (>{_LONG_SENTENCE_WORDS} words): {long_sentences}")
    return count, "; ".join(parts)


def gds_issue_count(text: str) -> int:
    """Count GDS plain-English red flags. 0 = clean; higher = more issues."""
    return _gds_scan(text)[0]


def gds_findings(text: str) -> str:
    """Readable summary of the GDS red flags found ('' when clean)."""
    return _gds_scan(text)[1]


def analyse(text: str):
    """Return (reading_age, gds_issue_count, gds_findings) for a body of text."""
    count, findings = _gds_scan(text)
    return reading_age(text), count, findings
