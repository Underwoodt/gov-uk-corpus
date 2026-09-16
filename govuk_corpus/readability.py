"""Readability + GDS plain-English analysis of body text (stdlib only).

Two things per page:

- ``reading_age(text)`` — UK reading age = Flesch-Kincaid Grade Level + 5.
- ``scan(text)`` — a class-based plain-English audit. Each *class* of problem
  (words to avoid, nominalisations, vague language, …) is counted, the counts are
  turned into a severity-weighted **impact** (``weight × log2(1+count)`` so a few
  serious issues outweigh many trivial ones), and the impact is normalised by page
  length into a **1–5 star** quality rating (5 = healthy, 1 = poor).

Checks are declared once in ``CHECKS`` (name, weight, reason, matcher), so widening
the audit is a one-line addition. Deterministic and dependency-free, so it is
unit-testable and safe to run over the whole corpus. See ``docs/gds-audit.md``.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional

_WORD = re.compile(r"[A-Za-z]+")
_SENT_SPLIT = re.compile(r"[.!?]+")
LONG_SENTENCE_WORDS = 25          # GDS: keep sentences short
MIN_WORDS_FOR_RATING = 40         # too little text to rate fairly -> stars = None

# Star bands: weighted impact per 1,000 words (density) -> stars. Ascending; the
# first band the density falls under wins, else 1 star. INITIAL values — calibrate
# from the corpus distribution printed by `build_readability` after a full re-scan.
STAR_BANDS = [(10.0, 5), (20.0, 4), (35.0, 3), (55.0, 2)]


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


# ---- check term lists ----------------------------------------------------
# GOV.UK style guide "words to avoid" (a practical subset).
_WORDS_TO_AVOID = [
    "advancing", "collaborate", "combating", "commit", "countering", "deploy",
    "dialogue", "disincentivise", "empower", "facilitate", "foster", "impactful",
    "initiate", "leverage", "liaise", "overarching", "pledge", "robust",
    "streamline", "strengthening", "tackling", "transform", "utilise", "utilize",
    "utilising", "utilizing", "endeavour", "commence", "purchase", "additional",
    "regarding", "aforementioned", "henceforth", "hereby", "notwithstanding",
]
_PHRASES_TO_AVOID = [
    "in order to", "going forward", "at this moment in time", "please note",
    "with regard to", "in respect of", "prior to", "in the event that",
    "a number of", "ring fenced", "ring-fenced", "best practice", "deep dive",
]
# Nominalisations — noun-forms of verbs; use the verb instead.
_NOMINALISATIONS = [
    "implementation", "implementations", "completion", "completions", "provision",
    "provisions", "application", "applications", "utilisation", "utilisations",
    "consideration", "considerations", "requirement", "requirements", "assessment",
    "assessments", "notification", "notifications", "commencement", "commencements",
]
_VAGUE = [
    "some", "many", "often", "regularly", "normally", "usually", "generally",
    "approximately", "soon", "quickly", "various", "several", "etc",
]
_UNNECESSARY = [
    "very", "really", "actually", "basically", "currently", "in fact",
    "it is important to note that", "it should be noted that",
]
_THERE = ["there is", "there are"]
_GOV = ["we", "us", "our"]
_APPLICANT = ["applicant", "applicants"]
_NEGATIVE = ["you must not", "you should not", "you shouldn't"]
# Impersonal "it is <required/…> that" — the constructions that should be "you must".
_IT_IS_RE = re.compile(
    r"\bit is (?:required|necessary|essential|mandatory|important|expected|"
    r"recommended|advisable|possible|prohibited|forbidden)\b", re.I)


def _alt(terms: List[str]) -> "re.Pattern":
    """Case-insensitive, word-boundary alternation (longest term first)."""
    parts = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.I)


class Check:
    def __init__(self, key, name, weight, reason, *, terms=None, regex=None, long_sentence=False):
        self.key, self.name, self.weight, self.reason = key, name, weight, reason
        self.regex = regex if regex is not None else (_alt(terms) if terms else None)
        self.long_sentence = long_sentence

    def count(self, text: str, sentences: List[str]) -> int:
        if self.long_sentence:
            return sum(1 for s in sentences if len(_WORD.findall(s)) > LONG_SENTENCE_WORDS)
        return len(self.regex.findall(text)) if self.regex else 0


CHECKS: List[Check] = [
    Check("words_to_avoid", "Words to avoid", 2,
          "GOV.UK ‘words to avoid’ — jargon and management-speak; use plain words.",
          terms=_WORDS_TO_AVOID),
    Check("phrases_to_avoid", "Phrases to avoid", 1,
          "Wordy officialese — cut or simplify.", terms=_PHRASES_TO_AVOID),
    Check("long_sentences", "Over-long sentences", 3,
          f"Sentences over {LONG_SENTENCE_WORDS} words are hard to follow — keep them short.",
          long_sentence=True),
    Check("nominalisation", "Nominalisations", 3,
          "Nouns made from verbs (‘implementation’) make writing abstract — use the verb.",
          terms=_NOMINALISATIONS),
    Check("vague_language", "Vague language", 2,
          "Vague quantifiers (‘some’, ‘often’) reduce precision — be specific.",
          terms=_VAGUE),
    Check("unnecessary_words", "Unnecessary words", 1,
          "Filler words and phrases that add no meaning.", terms=_UNNECESSARY),
    Check("there_is_are", "There is / there are", 1,
          "‘There is/are’ is usually padding — rewrite directly.", terms=_THERE),
    Check("impersonal_it_is", "Impersonal ‘It is’", 2,
          "Impersonal ‘it is required that…’ hides who acts — use ‘you must’.",
          regex=_IT_IS_RE),
    Check("gov_focused", "Government-focused (we/us/our)", 2,
          "‘We/us/our’ writes for the department — write for the user (‘you’).",
          terms=_GOV),
    Check("applicant", "Applicant vs you", 3,
          "‘The applicant’ is third-person — address the reader as ‘you’.",
          terms=_APPLICANT),
    Check("negative_phrasing", "Negative phrasing", 2,
          "Negative instructions (‘you must not’) are harder to follow — phrase positively.",
          terms=_NEGATIVE),
]

# key -> {name, weight, reason} for callers that render the classes (the dashboard).
CHECK_META: Dict[str, dict] = {
    c.key: {"name": c.name, "weight": c.weight, "reason": c.reason} for c in CHECKS}


def _impact(counts: Dict[str, int]) -> float:
    """Severity-weighted impact: weight × log2(1+count), summed over classes."""
    return round(sum(CHECK_META[k]["weight"] * math.log2(1 + n)
                     for k, n in counts.items() if n), 2)


def stars_for(impact: float, words: int) -> Optional[int]:
    """1–5 quality rating (5 healthy → 1 poor) from length-normalised impact.
    None when the page is too short to rate fairly."""
    if words < MIN_WORDS_FOR_RATING:
        return None
    density = impact / (words / 1000.0) if words else 0.0
    for threshold, star in STAR_BANDS:
        if density <= threshold:
            return star
    return 1


def scan(text: str) -> dict:
    """Full plain-English audit of one page.

    Returns {"words", "counts": {class_key: count}, "impact", "stars"}. Only
    non-zero classes appear in ``counts``. ``stars`` is None for too-short text.
    """
    text = text or ""
    words = len(_WORD.findall(text))
    sentences = _SENT_SPLIT.split(text) if text else []
    counts = {}
    for c in CHECKS:
        n = c.count(text, sentences)
        if n:
            counts[c.key] = n
    impact = _impact(counts)
    return {"words": words, "counts": counts, "impact": impact,
            "stars": stars_for(impact, words)}


# ---- backwards-compatible helpers ---------------------------------------
def gds_issue_count(text: str) -> int:
    """Total raw red flags across all classes (0 = clean)."""
    return sum(scan(text)["counts"].values())


def gds_findings(text: str) -> str:
    """Readable class-level summary ('' when clean), e.g.
    'Words to avoid: 3; Vague language: 2'."""
    counts = scan(text)["counts"]
    if not counts:
        return ""
    return "; ".join(f"{CHECK_META[k]['name']}: {n}"
                     for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def analyse(text: str):
    """(reading_age, scan-dict) for one page — used by the corpus backfill."""
    return reading_age(text), scan(text)
