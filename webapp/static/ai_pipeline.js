/* Shared AI Pipeline behaviour for the _ai_pipeline.html partial.
 *
 * The Explainability page drives the partial with its own inline script (which also feeds the
 * funnel Sankey there); this file drives the SAME partial on the Shortlist page, where there is
 * no funnel/Sankey/Funnel-Results — so it's the runs UI on its own, lazily started via
 * window.AIPipeline.init(cid) when the AI Pipeline sub-tab is first opened.
 */
(function () {
  "use strict";
  let CID = null, bgTimer = null, hooks = {}, curNested = "active";
  const $ = (id) => document.getElementById(id);

  function fmtInt(n) { return (n == null || isNaN(n)) ? "0" : Number(n).toLocaleString(); }
  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
  function fmtDate(s) { return s ? String(s).slice(0, 19).replace("T", " ") : "—"; }
  function fmtElapsed(a, b) {
    if (!a || !b) return "—";
    let s = Math.round((new Date(b) - new Date(a)) / 1000);
    if (!isFinite(s) || s < 0) return "—";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }
  function pagesCell(r) {
    const p = fmtInt(r.pages);
    if (r.target == null) return p;
    const t = fmtInt(r.target);
    const drift = (Number(r.pages) || 0) - (Number(r.target) || 0);
    if (!drift) return `${p} <span class="pg-sub">/ ${t}</span>`;
    const sign = drift > 0 ? "+" : "−", mag = fmtInt(Math.abs(drift));
    const tip = drift > 0
      ? `This run evaluated ${p} pages; the shortlist now holds ${t} — ${mag} fewer than when it ran.`
      : `This run evaluated ${p} of ${t} pages in the current shortlist — ${mag} not covered (stopped early, or the shortlist has since grown).`;
    return `<span title="${esc(tip)}">${p} <span class="pg-sub">/ ${t}</span></span>`
         + ` <span class="pg-drift" title="${esc(tip)}">${sign}${mag}</span>`;
  }
  function runChip(r) {
    if (r.alive) return ` <span class="run-chip running" title="A process is evaluating this run (${esc(r.phase || "")})">● running</span>`;
    if (r.stalled) return ` <span class="run-chip stalled" title="Marked running but no process is working it — resume to finish">⚠ stalled</span>`;
    return "";
  }
  function statusPill(status) {
    const m = { complete: { t: "Complete", bg: "#cce2d8", c: "#005a30" },
                incomplete: { t: "Incomplete", bg: "#fdf3d8", c: "#7a5b00" },
                in_progress: { t: "In Progress", bg: "#d2e2f1", c: "#0b3d6b" } };
    return m[status] || m.incomplete;
  }

  function bgUiRunning(on) {
    const run = $("run-bg"), nw = $("new-run"), stop = $("stop-bg"), note = $("bg-note");
    if (run) run.disabled = on;
    if (nw) nw.disabled = on;
    if (stop) stop.hidden = !on;
    if (note) note.hidden = !on;
  }
  function renderBgStatus(s) {
    const st = $("eval-status");
    const done = s.done || 0, remaining = (s.remaining == null ? null : s.remaining);
    const total = (remaining == null ? null : done + remaining);
    if (total && total > 0) {
      $("eval-progress-wrap").hidden = false;
      $("eval-progress-bar").style.width = Math.min(100, (done / total) * 100).toFixed(1) + "%";
    }
    let msg = `Background ${s.phase ? "(" + s.phase + ") " : ""}— evaluated ${fmtInt(done)}`
            + (remaining != null ? ` · ${fmtInt(remaining)} left` : "")
            + (s.skipped ? ` · ${fmtInt(s.skipped)} skipped` : "")
            + ` · $${(s.cost || 0).toFixed(2)}`;
    if (s.spent_today != null) msg += ` · $${s.spent_today.toFixed(2)}/$${(s.budget || 0).toFixed(2)} today`;
    if (!s.running) {
      if (s.error) msg = "⚠️ Stopped: " + s.error;
      else if (s.stopped === "budget") msg += " · ⛔ stopped: daily budget reached";
      else if (s.stopped === "cap") msg += " · ⏸ reached the per-run page limit — Execute again to continue";
      else msg += " · ✅ finished";
    }
    if (st) st.textContent = msg;
  }
  async function pollBg() {
    let s;
    try { s = await (await fetch(`/api/categories/${CID}/evaluate-bg/status`)).json(); }
    catch (e) { return; }
    renderBgStatus(s);
    await loadRuns();
    if (hooks.onPoll) { try { hooks.onPoll(s); } catch (e) {} }   // host page extras (e.g. run-results)
    if (!s.running) { bgUiRunning(false); if (bgTimer) { clearInterval(bgTimer); bgTimer = null; } }
  }
  function beginBgPolling() {
    bgUiRunning(true);
    if (!bgTimer) bgTimer = setInterval(pollBg, 3000);
    pollBg();
  }

  async function loadActiveSummary() {
    const box = $("active-summary");
    let j;
    try { j = await (await fetch(`/api/categories/${CID}/active-run`)).json(); }
    catch (e) { return; }
    if (!j || !j.run) { if (box) box.hidden = true; return; }
    box.hidden = false;
    $("as-name").textContent = j.run.name || j.run.run_id;
    const p = statusPill(j.status), el = $("as-status");
    el.textContent = p.t; el.style.background = p.bg; el.style.color = p.c;
    $("as-rows").innerHTML = (j.chain || []).map(r =>
      `<tr><td>${esc(r.phase || "—")}</td><td>${esc(r.provider || "—")}</td>`
      + `<td class="mono">${esc(r.model || "—")}</td>`
      + `<td class="num">${fmtInt(r.pages)}</td><td class="num">${fmtInt(r.kept)}</td>`
      + `<td class="num">${fmtInt(r.dropped)}</td><td class="num">${fmtInt(r.in_tokens)}</td>`
      + `<td class="num">${fmtInt(r.out_tokens)}</td><td class="num">$${(r.cost || 0).toFixed(2)}</td></tr>`).join("");
    if (j.status === "in_progress") {
      const run = $("run-bg"); if (run) run.disabled = true;
      if (!bgTimer) beginBgPolling();
    }
  }

  async function loadRuns() {
    let j;
    try { j = await (await fetch(`/api/categories/${CID}/runs`)).json(); }
    catch (e) { return; }
    if (window.setSqlPanel) setSqlPanel("runs-sql", j.sql);
    const activeRun = j.active || null;
    const line = $("active-run-line");
    if (line) line.textContent = activeRun
      ? `Active run: ${activeRun} (Execute Active Run adds to it; “Create New Run” to compare a different model).`
      : "No active run — “Execute Active Run” starts one with the model set in Settings.";
    // Stalled banner (a run left running with no live driver).
    const banner = $("stalled-banner");
    if (banner) {
      if (j.stalled && !j.any_running) {
        $("stalled-detail").textContent =
          `“${j.stalled.name || j.stalled.run_id}” (${j.stalled.phase || "inclusion"}) was left part-way through. `
          + `Complete the shortlist to resume it from where it stopped.`;
        banner.hidden = false;
      } else { banner.hidden = true; }
    }
    const table = $("runs-table"), tbody = table.querySelector("tbody");
    tbody.innerHTML = "";
    // A run is the whole pipeline: fold each Phase-2 (exclusion) into its Phase-1 (inclusion),
    // so the list shows ONE row per run — its name, and the pipeline outcome across both phases.
    const chains = j.runs.filter(r => !r.source_run_id).map(incl => {
      const excl = j.runs.find(x => x.source_run_id === incl.run_id) || null;
      return { incl, excl, phases: excl ? [incl, excl] : [incl] };
    });
    const sumF = (ph, f) => ph.reduce((a, x) => a + (Number(x[f]) || 0), 0);
    const chainWall = (ph) => {
      const fins = ph.map(x => x.finished_at).filter(Boolean);
      if (fins.length !== ph.length) return "—";                  // a phase hasn't finished
      const starts = ph.map(x => x.started_at).filter(Boolean).sort();
      return fmtElapsed(starts[0], fins.sort().slice(-1)[0]);
    };
    for (const c of chains) {
      const { incl, excl, phases } = c;
      const tail = excl || incl;                                  // the phase "Execute" continues
      const isActive = phases.some(p => p.run_id === activeRun);
      const chip = phases.some(p => p.alive) ? ' <span class="run-chip running" title="A process is evaluating this run">● running</span>'
                 : phases.some(p => p.stalled) ? ' <span class="run-chip stalled" title="Marked running but no process is working it">⚠ stalled</span>' : '';
      const phaseNote = excl ? ` <span class="pg-sub">(incl ${fmtInt(incl.pages)} → excl ${fmtInt(excl.pages)})</span>` : '';
      const tr = document.createElement("tr");
      if (isActive) tr.style.fontWeight = "700";
      tr.innerHTML =
        `<td><input type="radio" name="active-run" class="active-run" data-run="${tail.run_id}" ` +
        `${isActive ? "checked" : ""} title="Make this run active" ` +
        `style="width:18px;height:18px;accent-color:var(--black);"></td>` +
        `<td><a class="name-link" href="/categories/${CID}/runs/${incl.run_id}" title="Open run details">${esc(incl.name || incl.run_id)}</a>${chip}</td>` +
        `<td>${fmtDate(incl.started_at)}</td>` +
        `<td class="num timing">${chainWall(phases)}</td>` +
        `<td class="num">${fmtInt(incl.pages)}${phaseNote}</td>` +
        `<td class="num">${fmtInt((excl || incl).kept)}</td>` +
        `<td class="num">${fmtInt(sumF(phases, "dropped"))}</td>` +
        `<td class="num">${fmtInt(sumF(phases, "in_tokens"))}</td>` +
        `<td class="num">${fmtInt(sumF(phases, "out_tokens"))}</td>` +
        `<td class="num">$${sumF(phases, "cost").toFixed(2)}</td>` +
        `<td><a href="/categories/${CID}/download?stage=final" title="Download this shortlist's final results on the download page (pick columns/format there)">Download</a>` +
        `<a href="#" class="del-run" data-runs="${phases.map(p => p.run_id).join(",")}" data-pages="${sumF(phases, "pages")}" style="color:#d4351c;margin-left:14px;">Delete</a></td>`;
      tbody.appendChild(tr);
    }
    table.hidden = chains.length === 0;
    $("runs-empty").hidden = chains.length !== 0;

    table.querySelectorAll("a.del-run").forEach(a =>
      a.addEventListener("click", e => { e.preventDefault(); deleteRun(a.dataset.runs, a.dataset.pages); }));
    const lockRadios = !!($("stop-bg") && !$("stop-bg").hidden);
    table.querySelectorAll("input.active-run").forEach(radio => {
      radio.disabled = lockRadios;
      radio.addEventListener("change", async () => {
        if (!radio.checked) return;
        try { await fetch(`/api/categories/${CID}/runs/${radio.dataset.run}/activate`, { method: "POST" }); } catch (e) {}
        await loadRuns();
      });
    });
    loadActiveSummary();
    if (hooks.onRuns) { try { hooks.onRuns(j); } catch (e) {} }   // host page extras (Sankey, run-results)
  }

  async function newRun() {
    const btn = $("new-run");
    btn.disabled = true;
    try {
      const v = $("trial-variant"), c = $("trial-concurrency"), ca = $("trial-caching");
      const body = v ? { prompt_variant: v.value, concurrency: Number(c && c.value) || 1,
                         caching: !!(ca && ca.checked) } : {};
      const j = await (await fetch(`/api/categories/${CID}/runs`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
      if (j && j.run_id) { window.location.href = `/categories/${CID}/runs/${j.run_id}`; return; }
      await loadRuns();
      $("new-run-status").textContent = j && j.error ? ("Error: " + j.error) : "New run created.";
    } finally { btn.disabled = false; }
  }
  async function deleteRun(runIds, pages) {
    const ids = String(runIds || "").split(",").filter(Boolean);
    if (!ids.length) return;
    if (!confirm(`Delete this run (all phases) and its ${Number(pages).toLocaleString()} page decision(s)?\n\n`
      + `This permanently removes the run's keep/drop scores and its totals. It cannot be undone, `
      + `and re-creating them means re-running the AI (which costs money).\n\nYour daily spend/budget ledger is NOT affected.`)) return;
    for (const id of ids) {
      const res = await fetch(`/api/categories/${CID}/runs/${id}/delete`, { method: "POST" });
      if (!res.ok) { alert("Delete failed."); break; }
    }
    await loadRuns();
  }

  // Nested tabs inside the AI Pipeline: Active Run (controls) vs Run History (runs list).
  // `which` defaults to the current choice (lets the host re-apply it when the pipeline tab
  // becomes visible). pane-id is only claimed when the pipeline panel is actually showing.
  function showNested(which) {
    if (which === undefined) which = curNested;
    curNested = which;
    const btns = document.querySelectorAll(".subtab2");
    btns.forEach(b => b.classList.toggle("current", b.dataset.subtab2 === which));
    const a = $("tab2-active"), h = $("tab2-history");
    if (a) a.hidden = which !== "active";
    if (h) h.hidden = which !== "history";
    const active = [...btns].find(b => b.dataset.subtab2 === which);
    const pane = $("pane-id");
    const panel = document.querySelector(".ai-pipeline") && document.querySelector(".ai-pipeline").closest(".subpanel");
    if (pane && active && panel && !panel.hidden) pane.textContent = active.dataset.tabid;
    try { localStorage.setItem("ai-pipeline-subtab2-" + CID, which); } catch (e) {}
  }

  let wired = false;
  function wireOnce() {
    if (wired) return; wired = true;
    document.querySelectorAll(".subtab2").forEach(b =>
      b.addEventListener("click", e => { e.preventDefault(); showNested(b.dataset.subtab2); }));
    const run = $("run-bg"); if (run) run.addEventListener("click", async () => {
      run.disabled = true;
      try {
        const j = await (await fetch(`/api/categories/${CID}/evaluate-bg/start`, { method: "POST" })).json();
        if (j.error) { $("eval-status").textContent = "Error: " + j.error; run.disabled = false; return; }
        beginBgPolling();
      } catch (e) { $("eval-status").textContent = "Could not start background run."; run.disabled = false; }
    });
    const stop = $("stop-bg"); if (stop) stop.addEventListener("click", async () => {
      stop.disabled = true;
      try { await fetch(`/api/categories/${CID}/evaluate-bg/stop`, { method: "POST" }); } catch (e) {}
      stop.disabled = false;
      $("eval-status").textContent = "Stopping after the current batch…";
    });
    const complete = $("complete-shortlist"); if (complete) complete.addEventListener("click", async () => {
      complete.disabled = true;
      $("complete-status").textContent = "Resuming…";
      try {
        const j = await (await fetch(`/api/categories/${CID}/evaluate-bg/resume`, { method: "POST" })).json();
        if (j.error) { $("complete-status").textContent = "Error: " + j.error; complete.disabled = false; return; }
        $("stalled-banner").hidden = true;
        beginBgPolling();
      } catch (e) { $("complete-status").textContent = "Could not resume."; complete.disabled = false; }
    });
    const nw = $("new-run"); if (nw) nw.addEventListener("click", newRun);
  }

  window.AIPipeline = {
    loadRuns: () => loadRuns(),
    showNested: (which) => showNested(which),
    init(cid, options) {
      CID = cid;
      hooks = options || {};
      wireOnce();
      // Active Run is always the default; only an explicit #history hash (e.g. from the Run
      // performance sub-tab bar) opens Run History on load.
      const hash = (location.hash || "").replace("#", "");
      showNested(hash === "history" ? "history" : "active");
      loadRuns();
      // Resume showing progress if a background run is already going.
      (async () => {
        try { const s = await (await fetch(`/api/categories/${CID}/evaluate-bg/status`)).json(); if (s.running) beginBgPolling(); }
        catch (e) {}
      })();
    }
  };
})();
