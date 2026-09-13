"""Shared Streamlit helpers for the dashboard and its pages (auth + connection)."""
from __future__ import annotations

import os

import streamlit as st

from govuk_corpus.backend import db


def check_password() -> bool:
    """Password gate shared across pages (session-scoped once entered)."""
    pw = os.getenv("DASHBOARD_PASSWORD")
    if not pw:
        st.warning("No DASHBOARD_PASSWORD set — this app is open. Set one before exposing it publicly.")
        return True
    if st.session_state.get("authed"):
        return True
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        if st.form_submit_button("Enter"):
            if entered == pw:
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


def connect():
    """Open a connection to the active backend (SQLite locally, Postgres on the server).

    A 15s statement timeout guards the UI: no single query can hang the dashboard
    (the timeout is a Postgres feature; SQLite ignores the argument).
    """
    return db.connect(os.getenv("DASHBOARD_DB", "data/pilot.db"), statement_timeout_ms=15000)
