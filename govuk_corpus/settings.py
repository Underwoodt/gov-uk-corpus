"""Tiny key/value app settings (backend-agnostic)."""
from __future__ import annotations

from typing import Optional

from .backend import db

_IS_PG = db.__name__.endswith("db_pg")
_P = "%s" if _IS_PG else "?"


def get_setting(conn, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute(f"SELECT value FROM app_settings WHERE key = {_P}", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    if _IS_PG:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))
    else:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
