"""Per-page feedback — a star rating + comment left from the feedback widget on any page.

Backend-agnostic (SQLite dev / Postgres prod) like categories.py. Star questions are
clamped to 1-5; created_by/email are set from the signed-in user in accounts mode.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"

# The star questions, in display order: (field, question).
QUESTIONS = [
    ("q_functionality", "Does this page do what you need?"),
    ("q_ease", "Is it easy to use?"),
    ("q_quality", "Quality of the results / output?"),
]
_STAR_FIELDS = [f for f, _ in QUESTIONS]


def _star(v) -> Optional[int]:
    """Coerce a star value to an int in 1..5, or None."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 5 else None


def add_feedback(conn, *, page_id: str, page_title: str, feedback_text: str,
                 stars: Dict[str, Any], created_by: Optional[str] = None,
                 created_by_email: Optional[str] = None) -> int:
    """Store one feedback row and return its id. `stars` maps question field -> 1-5."""
    fid = int(time.time() * 1000)
    conn.execute(
        f"INSERT INTO page_feedback (id, page_id, page_title, feedback_text, "
        f"q_functionality, q_ease, q_quality, created_at, created_by, created_by_email) "
        f"VALUES ({_P},{_P},{_P},{_P},{_P},{_P},{_P},{_P},{_P},{_P})",
        (fid, (page_id or "")[:32], (page_title or "")[:200], (feedback_text or "")[:5000],
         _star(stars.get("q_functionality")), _star(stars.get("q_ease")),
         _star(stars.get("q_quality")), db.now_iso(), created_by, created_by_email))
    conn.commit()
    return fid


def list_feedback(conn, limit: int = 500) -> List[Dict[str, Any]]:
    """All feedback, newest first (for a future admin view)."""
    rows = conn.execute(
        f"SELECT id, page_id, page_title, feedback_text, q_functionality, q_ease, q_quality, "
        f"created_at, created_by_email FROM page_feedback ORDER BY created_at DESC LIMIT {_P}",
        (limit,)).fetchall()
    return [dict(r) for r in rows]
