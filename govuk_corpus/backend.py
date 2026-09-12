"""Database backend selector.

Stages import the active backend as `from .backend import db`. Selection is by
environment so the same stage code runs on both:
  - Postgres  when DB_BACKEND=postgres (or DB_HOST is set)  -> db_pg
  - SQLite    otherwise (the default; used by the pilot and tests) -> db

db_pg (and psycopg) is only imported when Postgres is selected, so SQLite runs and
the test suite need psycopg installed nowhere.
"""
from __future__ import annotations

import os

if os.getenv("DB_BACKEND", "").lower() == "postgres" or os.getenv("DB_HOST"):
    from . import db_pg as db  # noqa: F401
else:
    from . import db as db  # noqa: F401
