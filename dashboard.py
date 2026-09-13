"""GOV.UK Corpus — operations dashboard (Streamlit).

Run locally (SQLite pilot):
    streamlit run dashboard.py

On the server (Postgres via env), behind a password:
    set -a; . ~/gov-uk-corpus.env; set +a
    DASHBOARD_PASSWORD=... streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0

Same app will host the search / shortlist interface later (a second page).
Data queries live in govuk_corpus/metrics.py (unit-tested); this file is presentation.
"""
from __future__ import annotations

import os
import time

import streamlit as st

from govuk_corpus import metrics
from govuk_corpus.backend import db
from ui_common import check_password, connect

st.set_page_config(page_title="GOV.UK Corpus — Ops", page_icon="📊", layout="wide")

# ---- theme / styling ------------------------------------------------------
st.markdown(
    """
    <style>
      .kpi {background:#fff;border:1px solid #e6e9ee;border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 2px rgba(20,27,36,.05);height:100%;}
      .kpi .label{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:#6b7280;font-weight:600;}
      .kpi .value{font-size:1.9rem;font-weight:700;color:#111827;line-height:1.1;margin-top:4px;}
      .kpi .sub{font-size:.8rem;color:#6b7280;margin-top:2px;}
      .kpi.good{border-left:4px solid #16a34a;} .kpi.warn{border-left:4px solid #d97706;}
      .kpi.bad{border-left:4px solid #dc2626;}  .kpi.info{border-left:4px solid #2563eb;}
      .srcbar{background:#eef1f5;border-radius:6px;height:22px;overflow:hidden;margin:3px 0;}
      .srcbar>span{display:block;height:100%;}
      .pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.75rem;font-weight:600;}
      table.runs{border-collapse:collapse;width:100%;font-size:.9rem;}
      table.runs th{text-align:left;padding:8px 10px;color:#6b7280;font-size:.7rem;
                    text-transform:uppercase;letter-spacing:.05em;border-bottom:2px solid #e6e9ee;}
      table.runs td{padding:9px 10px;border-bottom:1px solid #eef1f5;}
    </style>
    """,
    unsafe_allow_html=True,
)

_SOURCE_COLORS = {
    "sitemap": "#2563eb", "redirect": "#a8492a", "attachment": "#b07d2b",
    "seed": "#6b7280", "other": "#9aa3b0", "(none)": "#cbd5e1",
}


@st.cache_data(ttl=30, show_spinner=False)
def load_data():
    conn = connect()
    try:
        return {
            "totals": metrics.corpus_totals(conn),
            "sources": metrics.source_breakdown(conn),
            "runs": metrics.recent_runs(conn, 7),
            "active": metrics.active_runs(conn),
        }
    finally:
        conn.close()


def kpi(col, label, value, sub="", tone="info"):
    col.markdown(
        f'<div class="kpi {tone}"><div class="label">{label}</div>'
        f'<div class="value">{value}</div><div class="sub">{sub}</div></div>',
        unsafe_allow_html=True,
    )


def _fmt_elapsed(sec):
    if sec is None:
        return "—"
    if sec < 60:
        return f"{sec:.0f}s"
    m, s = divmod(int(sec), 60)
    return f"{m}m {s:02d}s"


def _status_pill(status):
    color = {"complete": "#16a34a", "running": "#2563eb", "failed": "#dc2626"}.get(status, "#6b7280")
    bg = {"complete": "#dcfce7", "running": "#dbeafe", "failed": "#fee2e2"}.get(status, "#f1f5f9")
    return f'<span class="pill" style="background:{bg};color:{color}">{status}</span>'


def _rate_pill(rate):
    if rate >= 95:
        color, bg = "#16a34a", "#dcfce7"
    elif rate >= 80:
        color, bg = "#d97706", "#fef3c7"
    else:
        color, bg = "#dc2626", "#fee2e2"
    return f'<span class="pill" style="background:{bg};color:{color}">{rate:.0f}%</span>'


# ---- page -----------------------------------------------------------------
if not check_password():
    st.stop()

head_l, head_r = st.columns([4, 1])
head_l.title("📊 GOV.UK Corpus — Operations")
head_l.caption("Live view of the corpus and the crawl pipeline.")
if head_r.button("↻ Refresh"):
    load_data.clear()
    st.rerun()

data = load_data()
t = data["totals"]
runs = data["runs"]
total_errors = sum(r["errors"] for r in runs)

# Executive KPI row
c1, c2, c3, c4, c5 = st.columns(5)
kpi(c1, "Total pages", f"{t['total']:,}", f"{t['page_links']:,} parent→child links", "info")
fetch_tone = "good" if t["pct_fetched"] >= 95 else ("warn" if t["pct_fetched"] >= 60 else "bad")
kpi(c2, "Fetched", f"{t['fetched']:,}", f"{t['pct_fetched']}% of corpus", fetch_tone)
kpi(c3, "Backlog remaining", f"{t['backlog_remaining']:,}", "pages to fetch", "warn" if t["backlog_remaining"] else "good")
kpi(c4, "Redirects", f"{t['redirects']:,}", "resolved source→final", "info")
kpi(c5, "Active jobs", f"{data['active']}", "runs in progress", "info" if data["active"] else "good")

st.markdown("### ")
left, right = st.columns([1, 1])

# Source breakdown
with left:
    st.subheader("Where the corpus came from")
    srcs = data["sources"]
    grand = sum(n for _, n in srcs) or 1
    for name, n in srcs:
        pct = 100.0 * n / grand
        color = _SOURCE_COLORS.get(name, "#9aa3b0")
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;font-size:.85rem">'
            f'<b>{name}</b><span>{n:,} · {pct:.1f}%</span></div>'
            f'<div class="srcbar"><span style="width:{pct:.1f}%;background:{color}"></span></div>',
            unsafe_allow_html=True,
        )

# Pipeline health summary
with right:
    st.subheader("Pipeline health (last 7 runs)")
    ok = sum(1 for r in runs if r["status"] == "complete")
    failed = sum(1 for r in runs if r["status"] == "failed")
    h1, h2, h3 = st.columns(3)
    kpi(h1, "Completed", ok, "of last 7", "good")
    kpi(h2, "Failed", failed, "of last 7", "bad" if failed else "good")
    kpi(h3, "Errors", f"{total_errors:,}", "across those runs", "warn" if total_errors else "good")

# Recent runs table
st.subheader("Latest 7 runs")
if not runs:
    st.info("No runs recorded yet.")
else:
    rows_html = []
    for r in runs:
        rows_html.append(
            "<tr>"
            f"<td><code>{r['short_id']}</code></td>"
            f"<td>{r['stage'] or '—'}</td>"
            f"<td>{_status_pill(r['status'])}</td>"
            f"<td>{_fmt_elapsed(r['elapsed_s'])}</td>"
            f"<td>{r['processed']:,}</td>"
            f"<td>{r['errors']:,}</td>"
            f"<td>{_rate_pill(r['success_rate'])}</td>"
            f"<td style='color:#6b7280'>{(r['started_at'] or '')[:19].replace('T',' ')}</td>"
            "</tr>"
        )
    st.markdown(
        '<table class="runs"><tr><th>Run</th><th>Stage</th><th>Status</th><th>Elapsed</th>'
        '<th>Processed</th><th>Errors</th><th>Success</th><th>Started (UTC)</th></tr>'
        + "".join(rows_html) + "</table>",
        unsafe_allow_html=True,
    )

st.caption(f"Auto-cached 30s · rendered {time.strftime('%Y-%m-%d %H:%M:%S')} · "
           f"backend: {db.__name__.split('.')[-1]}")
