"""Shortlist Builder — CRUD for saved query specs ("categories"), with live execution.

Filter fields (departments × document types × keywords) run against the corpus to
produce a URL shortlist; the inference fields (include/exclude context, adjudication
hints, URL overrides) are saved for the downstream LLM phases.
"""
from __future__ import annotations

import csv
import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from govuk_corpus import categories as cat
from govuk_corpus import shortlist
from ui_common import check_password, connect

st.set_page_config(page_title="Shortlist Builder", page_icon="🔎", layout="wide")
if not check_password():
    st.stop()

conn = connect()

# field -> (kind, help). Sections drive layout & order.
HELP = {
    "owner_email": "Owner of the category; notified when a run finishes.",
    "description": "Goal of the shortlist (≤100 chars).",
    "dept_slugs": "Restrict to these departments. Comma or newline separated slugs (e.g. environment-agency, defra).",
    "document_type_slugs": "Restrict to these document types. Comma or newline separated (e.g. guidance, detailed_guide).",
    "keywords": "Shorten the list before the LLM phase. One per line, max 2 words per line.",
    "inclusion_context": "Describe what the inference tools should INCLUDE. One rule per line.",
    "exclusion_context": "Describe what the inference tools should EXCLUDE. One rule per line.",
    "adjudication_hints_keep": "Where include/exclude is unclear, agree to KEEP. One rule per line.",
    "adjudication_hints_drop": "Where include/exclude is unclear, agree to DROP. One rule per line.",
    "extra_guidance_urls": "Specific known guidance URLs that escape the search logic.",
    "only_use_extra_guidance_urls": "Skip the guidance relevance funnel; use only the URLs above.",
    "extra_law_urls": "Specific known law URLs that escape the search logic.",
    "only_use_extra_law_urls": "Skip the law relevance funnel; use only the URLs above.",
}
SECTIONS = [
    ("Header", [("owner_email", "email"), ("description", "text")]),
    ("Filters", [("dept_slugs", "area"), ("document_type_slugs", "area"), ("keywords", "area")]),
    ("Inference parameters", [("inclusion_context", "area"), ("exclusion_context", "area")]),
    ("Pass-2 adjudication", [("adjudication_hints_keep", "area"), ("adjudication_hints_drop", "area")]),
    ("Override search pipeline", [("extra_guidance_urls", "area"), ("only_use_extra_guidance_urls", "bool"),
                                  ("extra_law_urls", "area"), ("only_use_extra_law_urls", "bool")]),
]

mode = st.session_state.setdefault("sb_mode", "list")
sel_id = st.session_state.get("sb_id")


def _go(mode_, cid=None):
    st.session_state["sb_mode"] = mode_
    if cid is not None:
        st.session_state["sb_id"] = cid
    st.rerun()


def render_form(existing):
    data = {}
    with st.form("cat_form", clear_on_submit=False):
        for section, fields in SECTIONS:
            st.markdown(f"##### {section}")
            for f, kind in fields:
                lbl = cat.label(f)
                cur = (existing or {}).get(f)
                if kind == "email":
                    data[f] = st.text_input(lbl, value=cur or "", help=HELP.get(f))
                elif kind == "text":
                    data[f] = st.text_input(lbl, value=cur or "", max_chars=100, help=HELP.get(f))
                elif kind == "area":
                    data[f] = st.text_area(lbl, value=cur or "", height=90, help=HELP.get(f))
                elif kind == "bool":
                    data[f] = st.checkbox(lbl, value=bool(cur), help=HELP.get(f))
        c1, c2 = st.columns([1, 1])
        saved = c1.form_submit_button("💾 Save", type="primary")
        cancelled = c2.form_submit_button("Cancel")
    return data, saved, cancelled


def run_and_show(c):
    orgs = cat.parse_list(c.get("dept_slugs"))
    dts = cat.parse_list(c.get("document_type_slugs"))
    kws = cat.parse_list(c.get("keywords"))

    # Guard: keyword search has no index yet, so a keyword-only query would scan the
    # whole corpus. Only apply keywords when a structured filter (dept/doc-type) has
    # already narrowed the set. Structured-only and unfiltered COUNT/limit are cheap.
    apply_keywords = bool(kws) and bool(orgs or dts)
    if kws and not apply_keywords:
        st.warning(
            "Keyword filtering is skipped here: keyword search over the whole corpus "
            "needs the full-text index (coming next). Add a Department or Document Type "
            "to use keywords now. Showing the department/document-type result only."
        )
    used_kws = kws if apply_keywords else []
    st.caption(
        f"Filter → departments: {orgs or '—'} · document types: {dts or '—'} · "
        f"keywords (any): {used_kws or '—'}"
    )
    filters = dict(organisations=orgs, document_types=dts, keywords=used_kws, match="any")
    n = shortlist.count(conn, **filters)
    st.metric("Matching pages in corpus", f"{n:,}")
    if n:
        rows = shortlist.shortlist_rows(conn, limit=10000, **filters)

        # CSV (url, title) + plain URL list
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["url", "title"])
        for r in rows:
            writer.writerow([r["url"], r["title"] or ""])
        d1, d2 = st.columns([1, 1])
        d1.download_button("⬇ Download CSV (url, title)", buf.getvalue(),
                           file_name=f"shortlist-{c['id']}.csv", mime="text/csv")
        d2.download_button("⬇ Download URLs (.txt)", "\n".join(r["url"] for r in rows),
                           file_name=f"shortlist-{c['id']}.txt", mime="text/plain")

        st.dataframe([{"url": r["url"], "title": r["title"]} for r in rows[:500]],
                     use_container_width=True, height=320)
        if len(rows) > 500:
            st.caption(f"Showing first 500 of {len(rows):,}. Download for the full list.")

    with st.expander("SQL query that was run"):
        sql, params = shortlist.build_query(include_title=True, limit=10000, **filters)
        st.code(sql, language="sql")
        st.caption(f"Parameters: {params}")


# ---------------------------------------------------------------- LIST
if mode == "list":
    top_l, top_r = st.columns([4, 1])
    top_l.title("🔎 Shortlist Builder")
    top_l.caption("Saved query specs that filter the corpus and drive the inference pipeline.")
    if top_r.button("➕ New shortlist", type="primary"):
        st.session_state["sb_id"] = None
        _go("create")

    rows = cat.list_categories(conn)
    if not rows:
        st.info("No shortlists yet. Click **New shortlist** to create one.")
    else:
        st.dataframe(
            [{"ID": r["id"], "Description": (r["description"] or "")[:60], "Owner": r["owner_email"],
              "Departments": (r["dept_slugs"] or "")[:40], "Doc types": (r["document_type_slugs"] or "")[:40],
              "Status": r["status"], "Created": (r["created_at"] or "")[:10]} for r in rows],
            use_container_width=True, hide_index=True,
        )
        options = {f"{r['id']} — {(r['description'] or '')[:50]}": r["id"] for r in rows}
        pick = st.selectbox("Open a shortlist", ["—"] + list(options))
        if pick != "—":
            _go("view", options[pick])

# ---------------------------------------------------------------- CREATE / EDIT
elif mode in ("create", "edit"):
    existing = cat.get_category(conn, sel_id) if mode == "edit" else None
    st.title("✏️ " + ("Edit shortlist" if mode == "edit" else "New shortlist"))
    data, saved, cancelled = render_form(existing)
    if cancelled:
        _go("list")
    if saved:
        errors = cat.validate(data)
        if errors:
            for e in errors:
                st.error(e)
        else:
            if mode == "edit":
                cat.update_category(conn, sel_id, data)
                st.success("Changes saved.")
                _go("view", sel_id)
            else:
                cid = cat.create_category(conn, data)
                st.success("Shortlist created.")
                _go("view", cid)

# ---------------------------------------------------------------- VIEW / RUN
elif mode == "view":
    c = cat.get_category(conn, sel_id)
    if not c:
        _go("list")
    st.title(f"📄 {(c['description'] or 'Shortlist')[:70]}")
    b1, b2, b3, b4 = st.columns([1, 1, 1, 3])
    if b1.button("✏️ Edit"):
        _go("edit", sel_id)
    publish_label = "Unpublish" if c["status"] == "published" else "Publish"
    if b2.button(f"📢 {publish_label}"):
        cat.update_category(conn, sel_id, c, status="draft" if c["status"] == "published" else "published")
        _go("view", sel_id)
    if b3.button("🗑 Delete"):
        st.session_state["confirm_delete"] = True
    if b4.button("← Back to list"):
        _go("list")

    if st.session_state.get("confirm_delete"):
        st.warning("Delete this shortlist permanently?")
        d1, d2, _ = st.columns([1, 1, 4])
        if d1.button("Yes, delete", type="primary"):
            cat.delete_category(conn, sel_id)
            st.session_state["confirm_delete"] = False
            _go("list")
        if d2.button("Cancel"):
            st.session_state["confirm_delete"] = False
            st.rerun()

    st.markdown(f"**Status:** `{c['status']}`  ·  **Owner:** {c['owner_email']}  ·  **Created:** {(c['created_at'] or '')[:19].replace('T',' ')}")

    st.subheader("▶ Run filter against the corpus")
    run_and_show(c)

    st.subheader("Specification")
    for section, fields in SECTIONS:
        with st.expander(section, expanded=(section in ("Header", "Filters"))):
            for f, kind in fields:
                v = c.get(f)
                if kind == "bool":
                    st.markdown(f"**{cat.label(f)}:** {'Yes' if v else 'No'}")
                else:
                    st.markdown(f"**{cat.label(f)}:**")
                    st.markdown(f"> {v}" if v else "> _(not provided)_")
