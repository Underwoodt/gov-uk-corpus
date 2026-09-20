"""Plain-English explanation of what a keyword term will actually match.

Keyword matching is Postgres full-text (see shortlist._keyword_clause): each word is reduced
to its root so endings don't matter, very common words ("of", "the", …) are ignored, and a
multi-word term must appear as an adjacent phrase. That's opaque to anyone who doesn't know
about stemming / stop words, so this turns a term into a sentence a non-technical user can read
(e.g. "Markings" → matches "markings" or any word with the same root; "Export Health
Certificate" → those three words together, in order).

Backend-aware: on Postgres it uses the real analyzer (ts_lexize / phraseto_tsquery); on SQLite
(dev/tests) there's no stemmer, so it falls back to a simpler, still-accurate description.
"""
from __future__ import annotations

from typing import Dict, List

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")


def _quote(words: List[str]) -> str:
    """Join words as “a”, “b” and “c”."""
    q = [f"“{w}”" for w in words]
    if len(q) <= 1:
        return "".join(q)
    return ", ".join(q[:-1]) + " and " + q[-1]


def _classify_pg(conn, words: List[str]):
    """(content_words, stop_words) using the Postgres english stemmer: a word with a stem is a
    content word, a word the analyzer drops (empty result) is a stop word."""
    content, stops = [], []
    for w in words:
        row = conn.execute(
            "SELECT to_jsonb(ts_lexize('english_stem'::regdictionary, %s)) AS v", (w.lower(),)
        ).fetchone()
        lex = dict(row)["v"]
        (stops if lex == [] else content).append(w)
    return content, stops


def _phraseto(conn, term: str):
    try:
        row = conn.execute("SELECT phraseto_tsquery('english', %s)::text AS v", (term,)).fetchone()
        return dict(row)["v"] or None
    except Exception:
        return None


def explain_term(conn, term: str) -> Dict:
    """A plain-English description of what one keyword term matches."""
    words = term.split()
    if _IS_PG:
        content, stops = _classify_pg(conn, words)
        tsquery = _phraseto(conn, term)
    else:                              # SQLite dev: no stemmer/stop-word list available
        content, stops = words, []
        tsquery = None
    n = len(content)
    endings = "Word endings don’t matter — different forms of the word match too." if _IS_PG else \
        "The words are matched as written."
    caps = "Capital letters are ignored."
    stop_note = ""
    if stops:
        stop_note = (f" The common word{'s' if len(stops) > 1 else ''} {_quote(stops)} "
                     f"{'are' if len(stops) > 1 else 'is'} ignored"
                     + (", and may sit between the other words." if n >= 2 else "."))

    if n == 0:
        plain = ("Every word here is a very common word that search ignores, so on its own this "
                 "term won’t match anything. Try a more specific word.")
    elif n == 1:
        plain = (f"Matches a page that contains {_quote(content)} anywhere in its text. "
                 f"{endings} {caps}{stop_note}")
    else:
        plain = (f"A phrase. Matches a page where {_quote(content)} appear "
                 f"together, in this order. {endings} {caps}{stop_note}")
    return {"term": term, "content_words": content, "stop_words": stops,
            "is_phrase": n >= 2, "tsquery": tsquery, "plain": plain}


def explain_terms(conn, terms) -> List[Dict]:
    """Explanations for a list of keyword terms (skips blanks, de-dupes preserving order)."""
    out, seen = [], set()
    for t in terms or []:
        t = (t or "").strip()
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(explain_term(conn, t))
    return out
