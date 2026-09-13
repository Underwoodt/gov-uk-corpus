"""Shortlist Builder — categories list + 3-tab category view (Summary / Edit / Run).

Filter fields (departments × document types × keywords) run against the corpus to
produce a URL shortlist; inference fields are saved for the downstream LLM phases.
"""
from __future__ import annotations

import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from govuk_corpus import categories as cat
from govuk_corpus import shortlist
from ui_common import check_password, connect

st.set_page_config(page_title="Shortlist Builder", page_icon="🔎", layout="wide")
if not check_password():
    st.stop()

conn = connect()

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
        saved = st.form_submit_button("💾 Save", type="primary")
    return data, saved


def _filters(c):
    orgs = cat.parse_list(c.get("dept_slugs"))
    dts = cat.parse_list(c.get("document_type_slugs"))
    kws = cat.parse_list(c.get("keywords"))
    return orgs, dts, kws


def tab_summary(c):
    orgs, dts, kws = _filters(c)
    st.markdown("##### Selection funnel")
    st.caption("How each filter narrows the corpus (fast indexed counts; keyword via full-text index).")
    funnel = shortlist.selection_funnel(conn, organisations=orgs, document_types=dts, keywords=kws)
    st.table([{"Selection criterion": lbl, "Rows returned": f"{n:,}"} for lbl, n in funnel])


def tab_edit(c):
    st.caption("Update the saved specification.")
    data, saved = render_form(c)
    if saved:
        errors = cat.validate(data)
        if errors:
            for e in errors:
                st.error(e)
        else:
            cat.update_category(conn, c["id"], data)
            st.success("Changes saved.")
            _go("view", c["id"])


def tab_run(c):
    orgs, dts, kws = _filters(c)

    st.markdown("##### Summary")
    funnel = shortlist.selection_funnel(conn, organisations=orgs, document_types=dts, keywords=kws)
    st.table([{"Selection criterion": lbl, "Rows returned": f"{n:,}"} for lbl, n in funnel])

    filters = dict(organisations=orgs, document_types=dts, keywords=kws, match="any")
    n = shortlist.count(conn, **filters)
    st.markdown("##### Result")
    st.metric("Pages in this shortlist", f"{n:,}")
    if n:
        rows = shortlist.shortlist_rows(conn, limit=10000, **filters)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["url", "title"])
        for r in rows:
            w.writerow([r["url"], r["title"] or ""])
        d1, d2 = st.columns(2)
        d1.download_button("⬇ Download CSV (url, title)", buf.getvalue(),
                           file_name=f"shortlist-{c['id']}.csv", mime="text/csv")
        d2.download_button("⬇ Download URLs (.txt)", "\n".join(r["url"] for r in rows),
                           file_name=f"shortlist-{c['id']}.txt", mime="text/plain")
        st.dataframe([{"url": r["url"], "title": r["title"]} for r in rows[:500]],
                     use_container_width=True, height=300)
        if len(rows) > 500:
            st.caption(f"Showing first 500 of {len(rows):,}. Download for the full list.")

    with st.expander("SQL query that was run"):
        sql, params = shortlist.build_query(include_title=True, limit=10000, **filters)
        st.code(sql, language="sql")
        st.caption(f"Parameters: {params}")


# ================================================================= LIST
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
        ratios = [1.6, 3, 2.4, 2.4, 1.1, 1.1]
        h = st.columns(ratios)
        for col, name in zip(h, ["ID", "Description", "Owner", "Departments", "Status", ""]):
            col.markdown(f"**{name}**")
        st.divider()
        for r in rows:
            c0, c1, c2, c3, c4, c5 = st.columns(ratios)
            c0.write(str(r["id"]))
            c1.write((r["description"] or "")[:60])
            c2.write(r["owner_email"] or "")
            c3.write((r["dept_slugs"] or "")[:40])
            c4.write(r["status"] or "")
            if c5.button("Open", key=f"open_{r['id']}"):
                _go("view", r["id"])

# ================================================================= CREATE
elif mode == "create":
    st.title("✏️ New shortlist")
    if st.button("← Back to list"):
        _go("list")
    data, saved = render_form(None)
    if saved:
        errors = cat.validate(data)
        if errors:
            for e in errors:
                st.error(e)
        else:
            cid = cat.create_category(conn, data)
            st.success("Shortlist created.")
            _go("view", cid)

# ================================================================= VIEW (tabs)
elif mode == "view":
    c = cat.get_category(conn, sel_id)
    if not c:
        _go("list")
    hl, hr = st.columns([3, 2])
    hl.title(f"📄 {(c['description'] or 'Shortlist')[:60]}")
    hl.caption(f"Status: `{c['status']}` · Owner: {c['owner_email']} · "
               f"Created: {(c['created_at'] or '')[:19].replace('T', ' ')}")
    b1, b2, b3 = hr.columns(3)
    if b1.button("← List"):
        _go("list")
    publish_label = "Unpublish" if c["status"] == "published" else "Publish"
    if b2.button(publish_label):
        cat.update_category(conn, sel_id, c, status="draft" if c["status"] == "published" else "published")
        _go("view", sel_id)
    if b3.button("🗑 Delete"):
        st.session_state["confirm_delete"] = True

    if st.session_state.get("confirm_delete"):
        st.warning("Delete this shortlist permanently?")
        d1, d2, _ = st.columns([1, 1, 5])
        if d1.button("Yes, delete", type="primary"):
            cat.delete_category(conn, sel_id)
            st.session_state["confirm_delete"] = False
            _go("list")
        if d2.button("Cancel"):
            st.session_state["confirm_delete"] = False
            st.rerun()

    t_summary, t_edit, t_run = st.tabs(["📊 Summary", "✏️ Edit Category & Save", "▶ Run the Pipeline"])
    with t_summary:
        tab_summary(c)
    with t_edit:
        tab_edit(c)
    with t_run:
        tab_run(c)
