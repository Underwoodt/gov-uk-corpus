"""GOV.UK-styled Shortlist Builder — FastAPI + Jinja front end.

Renders the Categories list and the Create/Edit + Preview pages in the Defra /
GOV.UK Design System style, reusing the existing data layer (categories, facets,
shortlist). Backend is selected by env (SQLite locally, Postgres on the server).

Run locally:
    CORPUS_DB=data/pilot.db uvicorn webapp.app:app --reload --port 8600
On the server (Postgres):
    set -a; . ~/gov-uk-corpus.env; set +a
    uvicorn webapp.app:app --host 0.0.0.0 --port 8600
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from govuk_corpus import ai_models, audit
from govuk_corpus import categories as cat
from govuk_corpus import evaluate, orgs, settings, shortlist
from govuk_corpus.backend import db

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("CORPUS_DB", "data/pilot.db")
PASSWORD = os.getenv("DASHBOARD_PASSWORD")
COOKIE = "sb_auth"

app = FastAPI(title="Shortlist Builder")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

# ---- example presets for "start from an example" -------------------------
EXAMPLES: Dict[str, dict] = {
    "slurry": {
        "slug": "slurry_example",
        "dept_slugs": ["environment-agency", "rural-payments-agency"],
        "document_type_slugs": ["guidance", "detailed_guide"],
        "keywords": "slurry\nlagoon\ndigestate\ndirty water\nsilage effluent",
        "inclusion_context": ("Slurry is liquid or semi-liquid livestock manure: cattle and pig slurry, "
                              "dirty water, digestate from farm anaerobic digestion, and silage effluent. "
                              "Include a page if it mentions slurry in this farming sense anywhere, even once."),
        "exclusion_context": ("Exclude pages where slurry means something other than livestock manure: coal, "
                             "mining, concrete slurry. Exclude sewage sludge and biosolids."),
        "adjudication_hints_keep": "Grant page that funds slurry stores even if the rest is about payments",
        "adjudication_hints_drop": "Sewage sludge or biosolids guidance for treatment works",
    },
    "fish": {
        "slug": "fish_example",
        "dept_slugs": ["marine-management-organisation"],
        "document_type_slugs": ["guidance"],
        "keywords": "catch certificate\nfishery products\nlanding declaration",
        "inclusion_context": "Commercial sea fisheries: catching, landing, and exporting fishery products.",
        "exclusion_context": "Exclude angling as a hobby and 'phishing' security pages.",
        "adjudication_hints_keep": "",
        "adjudication_hints_drop": "",
    },
}

_META_CACHE: Dict[str, str] = {}

# ---- count cache ---------------------------------------------------------
# The corpus changes ~daily but the funnel is viewed constantly, so cache each
# distinct count for COUNT_CACHE_TTL seconds (0 disables). Keyed by the filter
# set, not the connection, since the underlying data is the same DB.
_COUNT_CACHE: Dict[tuple, tuple] = {}
_COUNT_TTL = int(os.getenv("COUNT_CACHE_TTL", "300"))
_COUNT_LOCK = threading.Lock()


def _count_key(filters: dict) -> tuple:
    return (tuple(sorted(filters.get("organisations") or [])),
            tuple(sorted(filters.get("document_types") or [])),
            tuple(sorted(filters.get("keywords") or [])),
            filters.get("match", "all"))


def cached_count(conn, **filters) -> int:
    """shortlist.count with a short TTL cache keyed by the filter set."""
    if _COUNT_TTL <= 0:
        return shortlist.count(conn, **filters)
    key = _count_key(filters)
    now = time.time()
    with _COUNT_LOCK:
        hit = _COUNT_CACHE.get(key)
        if hit and hit[1] > now:
            return hit[0]
    n = shortlist.count(conn, **filters)
    with _COUNT_LOCK:
        _COUNT_CACHE[key] = (n, now + _COUNT_TTL)
    return n

# Prefilled into the Page Types box on a new category (edit/clear as needed).
DEFAULT_DOC_TYPES = "\n".join([
    "guidance", "detailed_guide", "statutory_guidance", "document_collection",
    "manual_section", "html_publication", "guide", "manual",
])


# ---- helpers -------------------------------------------------------------
def connect():
    return db.connect(DB_PATH, statement_timeout_ms=15000) if _is_pg() else db.connect(DB_PATH)


def _is_pg() -> bool:
    return db.__name__.endswith("db_pg")


def corpus_total(conn) -> Optional[int]:
    """Total usable pages — computed once and cached (also drives the top banner)."""
    if "n" not in _META_CACHE:
        try:
            _META_CACHE["n"] = conn.execute(
                "SELECT COUNT(*) AS n FROM content WHERE is_redirect = 0 AND content_hash IS NOT NULL"
            ).fetchone()["n"]
        except Exception:
            _META_CACHE["n"] = None
    return _META_CACHE["n"]


def corpus_meta(conn) -> str:
    n = corpus_total(conn)
    return f"Newest snapshot · {n:,} pages" if n is not None else "Newest snapshot"


def ctx(conn, request: Request, **extra) -> dict:
    base = {"request": request, "corpus_meta": corpus_meta(conn), "active_nav": "categories"}
    base.update(extra)
    return base


# ---- AI Assistant (temporary prototype) ---------------------------------
# Provider is chosen in the UI (Settings page) and stored in app_settings.
# API keys still come from env; the toggle just picks which provider to use.
PROVIDERS = {
    "anthropic": {"label": "Anthropic (Claude)", "base_url": "",
                  "model": "claude-haiku-4-5-20251001",
                  "key_envs": ("ANTHROPIC_API_KEY", "AI_API_KEY"),
                  "price_in": 1.0, "price_out": 5.0},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/anthropic",
                 "model": "deepseek-chat",
                 "key_envs": ("DEEPSEEK_API_KEY", "AI_API_KEY"),
                 "price_in": 0.27, "price_out": 1.10},
}
DEFAULT_PROVIDER = "anthropic"


def _provider_key(provider: str) -> Optional[str]:
    for env in PROVIDERS[provider]["key_envs"]:
        v = os.getenv(env)
        if v:
            return v
    return None


DEFAULT_DAILY_BUDGET = 20.0     # USD/day
DEFAULT_MAX_DOCS = 600          # documents per evaluation run


def _budget(conn) -> float:
    try:
        return float(settings.get_setting(conn, "ai_daily_budget", str(DEFAULT_DAILY_BUDGET)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_BUDGET


def _max_docs(conn) -> int:
    try:
        return int(settings.get_setting(conn, "ai_max_docs_per_run", str(DEFAULT_MAX_DOCS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DOCS


def _daily_spend(conn) -> float:
    day = db.now_iso()[:10]
    row = conn.execute(f"SELECT COALESCE(SUM(cost), 0) AS c FROM ai_usage WHERE day = {'%s' if _is_pg() else '?'}",
                       (day,)).fetchone()
    return float(row["c"] or 0.0)


def _log_ai_usage(conn, cost, in_tok, out_tok, kind: str) -> None:
    ts = db.now_iso()
    ph = "%s" if _is_pg() else "?"
    conn.execute(
        f"INSERT INTO ai_usage (day, created_at, cost, input_tokens, output_tokens, kind) "
        f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
        (ts[:10], ts, cost or 0.0, in_tok or 0, out_tok or 0, kind))
    conn.commit()


def _ai_config(conn) -> dict:
    """Resolve the active AI model (from the ai_models table) + provider env."""
    ai_models.seed_defaults(conn)
    active = settings.get_setting(conn, "active_model_id", "")
    m = ai_models.get_model(conn, active) if active else None
    if not m:
        models = ai_models.list_models(conn)
        m = models[0] if models else None
    if not m:   # nothing configured — fall back to the built-in default
        p = PROVIDERS[DEFAULT_PROVIDER]
        return {"provider": DEFAULT_PROVIDER, "label": p["label"], "base_url": p["base_url"],
                "model": p["model"], "key": _provider_key(DEFAULT_PROVIDER),
                "has_key": _provider_key(DEFAULT_PROVIDER) is not None,
                "price_in": p["price_in"], "price_out": p["price_out"]}
    provider = m["provider"]
    p = PROVIDERS.get(provider) or PROVIDERS[DEFAULT_PROVIDER]
    return {"provider": provider, "label": p["label"], "base_url": p["base_url"],
            "model": m["model_id"], "key": _provider_key(provider),
            "has_key": _provider_key(provider) is not None,
            "price_in": m["input_per_m"], "price_out": m["output_per_m"]}


def _ai_reply(config: dict, system: str, prompt: str) -> dict:
    if not config.get("key"):
        return {"error": f"No API key set for {config['label']}. Add its key to "
                f"~/gov-uk-corpus.env and restart, or pick a provider that has one in Settings."}
    try:
        import anthropic
    except Exception:
        return {"error": "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"}
    try:
        client_kwargs = dict(api_key=config["key"], timeout=float(os.getenv("AI_TIMEOUT", "45")),
                             max_retries=0)
        if config["base_url"]:               # empty => anthropic SDK default (Claude API)
            client_kwargs["base_url"] = config["base_url"]
        # Org-scoped ("Default") Anthropic keys need the workspace id header.
        ws = os.getenv("ANTHROPIC_WORKSPACE_ID")
        if ws and config["provider"] == "anthropic":
            client_kwargs["default_headers"] = {"anthropic-workspace-id": ws}
        client = anthropic.Anthropic(**client_kwargs)
        kwargs = dict(model=config["model"], max_tokens=1024,
                      messages=[{"role": "user", "content": prompt}])
        if system.strip():
            kwargs["system"] = system.strip()
        msg = client.messages.create(**kwargs)
        text = "".join(getattr(b, "text", "") for b in msg.content)
        actual_model = getattr(msg, "model", None)   # what the API actually served
        usage = getattr(msg, "usage", None)
        in_tok = getattr(usage, "input_tokens", None)
        out_tok = getattr(usage, "output_tokens", None)
        cost = None
        if in_tok is not None and out_tok is not None:
            cost = round((in_tok / 1e6) * config["price_in"]
                         + (out_tok / 1e6) * config["price_out"], 6)
        return {"reply": text, "model": config["model"], "actual_model": actual_model,
                "provider": config["provider"],
                "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost,
                "price_input_per_m": config["price_in"], "price_output_per_m": config["price_out"]}
    except Exception as e:  # network / auth / API errors surfaced to the page
        logging.getLogger("assistant").exception("AI call failed (provider=%s model=%s)",
                                                 config.get("provider"), config.get("model"))
        return {"error": f"{type(e).__name__}: {e}"}


def authed(request: Request) -> bool:
    if not PASSWORD:
        return True
    token = request.cookies.get(COOKIE, "")
    want = hmac.new(PASSWORD.encode(), b"ok", hashlib.sha256).hexdigest()
    return hmac.compare_digest(token, want)


def login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url=str(request.url_for("login")), status_code=303)


def form_values(form) -> dict:
    """Flatten a submitted form into a category data dict."""
    d = {k: (form.get(k) or "").strip() for k in
         ("slug", "owner_email", "dept_slugs", "document_type_slugs", "keywords",
          "inclusion_context", "exclusion_context",
          "adjudication_hints_keep", "adjudication_hints_drop")}
    d["include_child_orgs"] = form.get("include_child_orgs")  # checkbox: "on" or absent
    d["description"] = cat.prettify(d.get("slug"))  # keep the Streamlit list name sensible
    return d


# ---- auth ---------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login(request: Request, bad: int = 0):
    body = ("<div class='error-summary'><h2>There is a problem</h2>"
            "<ul><li>Incorrect password.</li></ul></div>" if bad else "")
    return HTMLResponse(
        f"""<!doctype html><meta charset=utf-8>
        <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M4 1h5l3 3v11H4z' fill='%231d70b8'/><path d='M9 1v3h3z' fill='%23003078'/><g fill='none' stroke='%23fff' stroke-width='1' stroke-linecap='round'><path d='M6 7h5'/><path d='M6 9h5'/><path d='M6 11h4'/></g></svg>">
        <link rel=stylesheet href='/static/govuk.css'>
        <div class='masthead'><div class='wrap'><span class='brand'>Content Shortlist Builder</span></div></div>
        <div class='wrap body'><h1>Sign in</h1>{body}
        <form method=post action='{request.url_for('do_login')}'>
        <div class='field'><label class='q' for='p'>Password</label>
        <input id='p' name='password' type='password'></div>
        <button class='btn' type=submit>Sign in</button></form></div>""")


@app.post("/login")
def do_login(request: Request, password: str = Form("")):
    if PASSWORD and not hmac.compare_digest(password, PASSWORD):
        return RedirectResponse(url=str(request.url_for("login")) + "?bad=1", status_code=303)
    resp = RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    token = hmac.new((PASSWORD or "").encode(), b"ok", hashlib.sha256).hexdigest()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=86400)
    return resp


# ---- list ---------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def list_categories_page(request: Request, flash: str = ""):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    rows = cat.list_categories(conn)
    for r in rows:
        r["display_name"] = r.get("slug") and cat.prettify(r["slug"]) or (r.get("description") or "Untitled")
        r["updated"] = (r.get("updated_at") or r.get("created_at") or "")[:10] or "—"
        try:
            r["pages_kept"] = "{:,}".format(cached_count(
                conn,
                organisations=cat.parse_list(r.get("dept_slugs")),
                document_types=cat.parse_list(r.get("document_type_slugs")),
                keywords=[]))
        except Exception:
            r["pages_kept"] = "—"
    conn.close()
    return templates.TemplateResponse("list.html", ctx(connect(), request, categories=rows, flash=flash))


# ---- create -------------------------------------------------------------
@app.get("/categories/new", response_class=HTMLResponse)
def new_category_page(request: Request):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    return templates.TemplateResponse("form.html", _form_ctx(conn, request, None, {}, []))


@app.post("/categories/new")
async def create_category(request: Request):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    form = await request.form()
    data = form_values(form)
    errors = cat.validate(data)
    if errors:
        return templates.TemplateResponse("form.html", _form_ctx(conn, request, None, data, errors))
    cid = cat.create_category(conn, data)
    conn.close()
    return RedirectResponse(url=str(request.url_for("edit_category_page", cid=cid)), status_code=303)


# ---- edit ---------------------------------------------------------------
@app.get("/categories/{cid}/edit", response_class=HTMLResponse)
def edit_category_page(request: Request, cid: int):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    return templates.TemplateResponse("form.html", _form_ctx(conn, request, category, category, []))


@app.post("/categories/{cid}/edit")
async def update_category(request: Request, cid: int):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    form = await request.form()
    data = form_values(form)
    data["slug"] = category.get("slug")  # name is fixed after creation
    errors = cat.validate(data)
    if errors:
        merged = {**category, **data}
        return templates.TemplateResponse("form.html", _form_ctx(conn, request, category, merged, errors))
    cat.update_category(conn, cid, data)
    conn.close()
    return RedirectResponse(url=str(request.url_for("edit_category_page", cid=cid)), status_code=303)


def _form_ctx(conn, request, category, values, errors) -> dict:
    return ctx(
        conn, request,
        is_edit=category is not None,
        category=category,
        action=(str(request.url_for("update_category", cid=category["id"])) if category
                else str(request.url_for("create_category"))),
        v=values or {},
        default_doc_types=DEFAULT_DOC_TYPES,
        errors=errors,
        examples_json=json.dumps(EXAMPLES),
    )


# ---- preview / run ------------------------------------------------------
def _filters(category) -> dict:
    return dict(
        organisations=cat.parse_list(category.get("dept_slugs")),
        document_types=cat.parse_list(category.get("document_type_slugs")),
        keywords=cat.parse_list(category.get("keywords")),
        match="any",
    )


def _effective_filters(conn, category) -> dict:
    """Filters actually applied: when 'include child organisations' is set, expand
    the chosen orgs to include their child departments (all descendants)."""
    f = _filters(category)
    if category.get("include_child_orgs") and f["organisations"]:
        f["organisations"] = orgs.expand_with_children(conn, f["organisations"], recursive=True)
    return f


_FUNNEL_STAGES = {  # stage -> (label, which filters apply)
    "all":     ("All pages", ()),
    "org":     ("After organisation filter", ("organisations",)),
    "doctype": ("After document-type filter", ("organisations", "document_types")),
    "keyword": ("After keyword filter", ("organisations", "document_types", "keywords")),
}


@app.get("/categories/{cid}", response_class=HTMLResponse)
def preview_category_page(request: Request, cid: int):
    """Fast shell — the funnel and results load progressively via the JSON API below."""
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    filters = _effective_filters(conn, category)
    # The specific filter values applied at each funnel step (for the collapsed rows).
    stage_value_key = {"all": None, "org": "organisations",
                       "doctype": "document_types", "keyword": "keywords"}
    stages = [{"stage": k, "label": v[0],
               "vals": filters[stage_value_key[k]] if stage_value_key[k] else []}
              for k, v in _FUNNEL_STAGES.items()]
    # SQL preview is cheap (no DB hit) — render it inline.
    sql, params = shortlist.build_query(include_title=True, limit=10000, **filters)
    pretty = shortlist.pretty_sql(shortlist.interpolate_sql(sql, params))
    eval_max_docs = _max_docs(conn)
    conn.close()
    return templates.TemplateResponse("preview.html", ctx(
        connect(), request, category=category, sql=pretty, stages=stages,
        eval_max_docs=eval_max_docs))


@app.get("/api/categories/{cid}/funnel")
def api_funnel(request: Request, cid: int, stage: str = "all"):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    if stage not in _FUNNEL_STAGES:
        return JSONResponse({"error": "unknown stage"}, status_code=404)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        return JSONResponse({"error": "not found"}, status_code=404)
    label, applies = _FUNNEL_STAGES[stage]
    if stage == "all":
        # Same number as the top banner — reuse the cached corpus total, no query.
        n = corpus_total(conn)
        if n is None:
            n = cached_count(conn)
    else:
        filters = _effective_filters(conn, category)
        kw = {k: filters[k] for k in applies}
        if "keywords" in kw:
            kw["match"] = "any"
        n = cached_count(conn, **kw)
    conn.close()
    return JSONResponse({"stage": stage, "label": label, "count": n})


@app.get("/api/categories/{cid}/results")
def api_results(request: Request, cid: int, limit: int = 10, offset: int = 0):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    limit = limit if limit in (10, 50, 100) else 10
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        return JSONResponse({"error": "not found"}, status_code=404)
    filters = _effective_filters(conn, category)
    total = cached_count(conn, **filters)
    offset = max(0, min(offset, max(0, total - 1)))   # clamp within range
    rows = shortlist.detail_rows(conn, limit=limit, offset=offset, **filters) if total else []
    conn.close()
    return JSONResponse({"total": total, "shown": len(rows),
                         "offset": offset, "limit": limit, "rows": rows})


# ---- AI evaluation (inclusion pass over the shortlist), tracked per run --
def _cfg_for(conn, provider: str, model: str) -> dict:
    """Build an AI config for a run's fixed provider + model; prices from ai_models."""
    p = PROVIDERS.get(provider) or PROVIDERS[DEFAULT_PROVIDER]
    row = ai_models.find(conn, provider, model)
    price_in = row["input_per_m"] if row else p["price_in"]
    price_out = row["output_per_m"] if row else p["price_out"]
    return {"provider": provider, "label": p["label"], "base_url": p["base_url"],
            "model": model or p["model"], "key": _provider_key(provider),
            "price_in": price_in, "price_out": price_out}


def _active_run(conn, cid: int) -> str:
    return settings.get_setting(conn, f"active_run_{cid}", "") or ""


def _ensure_run(conn, cid: int) -> str:
    """Return the active run for the category, creating one (current provider/model) if none."""
    run_id = _active_run(conn, cid)
    if run_id and evaluate.get_run(conn, run_id):
        return run_id
    cfg = _ai_config(conn)
    run_id = evaluate.create_run(conn, cid, cfg["model"], cfg["provider"])
    settings.set_setting(conn, f"active_run_{cid}", run_id)
    return run_id


def _run_evaluation(cid: int, limit: int) -> dict:
    conn = connect()
    try:
        category = cat.get_category(conn, cid)
        if not category:
            return {"error": "Category not found."}
        budget = _budget(conn)
        spent = _daily_spend(conn)
        if budget > 0 and spent >= budget:
            return {"error": f"Daily AI budget of ${budget:.2f} reached (${spent:.4f} spent today). "
                    f"Raise it in Settings or try again tomorrow.",
                    "spent_today": round(spent, 4), "budget": budget, "stopped": "budget"}

        run_id = _ensure_run(conn, cid)
        run = evaluate.get_run(conn, run_id)
        cfg = _cfg_for(conn, run["provider"], run["model"])
        if not cfg["key"]:
            return {"error": f"No API key for {cfg['label']} (this run's provider) — set one in Settings."}

        limit = min(limit, _max_docs(conn))
        filters = _effective_filters(conn, category)
        inclusion = category.get("inclusion_context") or ""
        exclusion = category.get("exclusion_context") or ""
        rows = evaluate.run_candidates(conn, run_id, cid, limit, **filters)
        done = 0
        cost = 0.0
        stopped = None
        for r in rows:
            if budget > 0 and spent >= budget:
                stopped = "budget"
                break
            t0 = time.time()
            prompt = evaluate.build_prompt(inclusion, exclusion, r["title"], r["description"], r["body"])
            res = _ai_reply(cfg, "", prompt)
            ms = int((time.time() - t0) * 1000)
            if res.get("error"):
                return {"error": res["error"], "run_id": run_id, "evaluated_this_run": done,
                        "cost_usd": round(cost, 6), "spent_today": round(spent, 4), "budget": budget}
            evaluate.save_page(conn, run_id, cid, r["url"],
                               evaluate.parse_decision(res.get("reply", "")), ms)
            evaluate.set_actual_model(conn, run_id, res.get("actual_model"))
            c = res.get("cost_usd") or 0.0
            evaluate.add_run_cost(conn, run_id, c)
            _log_ai_usage(conn, c, res.get("input_tokens"), res.get("output_tokens"), "evaluate")
            done += 1
            cost += c
            spent += c
        total = cached_count(conn, **filters)
        run = evaluate.get_run(conn, run_id)
        remaining = max(0, total - (run["pages"] or 0))
        if remaining == 0:
            evaluate.finish_run(conn, run_id)
        warning = (f"Within 10% of the ${budget:.2f} daily budget (${spent:.4f} spent today)."
                   if budget > 0 and spent >= 0.9 * budget else None)
        return {"run_id": run_id, "model": run["model"], "provider": run["provider"],
                "evaluated_this_run": done, "cost_usd": round(cost, 6),
                "spent_today": round(spent, 4), "budget": budget,
                "warning": warning, "stopped": stopped, "remaining": remaining, "run": run}
    finally:
        conn.close()


@app.post("/api/categories/{cid}/runs")
async def api_new_run(request: Request, cid: int):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    if not cat.get_category(conn, cid):
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    cfg = _ai_config(conn)     # capture the current provider/model for the new run
    run_id = evaluate.create_run(conn, cid, cfg["model"], cfg["provider"])
    settings.set_setting(conn, f"active_run_{cid}", run_id)
    run = evaluate.get_run(conn, run_id)
    conn.close()
    return JSONResponse({"run_id": run_id, "run": run})


@app.post("/api/categories/{cid}/evaluate")
async def api_evaluate(request: Request, cid: int, limit: int = 10):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    limit = max(1, min(limit, 50))
    result = await run_in_threadpool(_run_evaluation, cid, limit)
    return JSONResponse(result)


@app.get("/categories/{cid}/performance", response_class=HTMLResponse)
def performance_page(request: Request, cid: int):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    resp = templates.TemplateResponse("performance.html", ctx(conn, request, category=category))
    conn.close()
    return resp


@app.get("/api/categories/{cid}/runs")
def api_list_runs(request: Request, cid: int):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    out = {"runs": evaluate.list_runs(conn, cid), "active": _active_run(conn, cid)}
    conn.close()
    return JSONResponse(out)


@app.get("/api/categories/{cid}/compare")
def api_compare(request: Request, cid: int, base: str = "", other: str = ""):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    out = evaluate.compare(conn, base, other) if base and other else {}
    conn.close()
    return JSONResponse(out)


@app.get("/api/categories/{cid}/runs/{run_id}/results")
def api_run_results(request: Request, cid: int, run_id: str, keep: str = ""):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    k = int(keep) if keep in ("0", "1") else None
    conn = connect()
    out = {"run": evaluate.get_run(conn, run_id),
           "rows": evaluate.run_results(conn, run_id, keep=k, limit=500)}
    conn.close()
    return JSONResponse(out)


@app.get("/categories/{cid}/runs/{run_id}/download")
def download_run(request: Request, cid: int, run_id: str):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    rows = evaluate.run_results(conn, run_id)
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["url", "decision", "score", "reason"])
    for r in rows:
        decision = "keep" if r["keep"] == 1 else "drop" if r["keep"] == 0 else "unparseable"
        w.writerow([r["url"], decision, r["score"], r["reason"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="run-{run_id}.csv"'})


# ---- download page (choose format + fields) -----------------------------
# (key, label, default_on, disabled, warning)
_DOWNLOAD_FIELDS = [
    ("url", "URL", True, True, None),
    ("title", "Title", True, False, None),
    ("size", "Size", True, False, None),
    ("readability", "Readability score", False, False, None),
    ("gds_issues", "GDS number of issues", False, False, None),
    ("gds_findings", "GDS issues text", False, False, None),
    ("content", "Content", False, False, "warning: may make the download very large"),
    ("first_published_at", "First published at", False, False, None),
    ("public_updated_at", "Public updated at", False, False, None),
]


@app.get("/categories/{cid}/download", response_class=HTMLResponse)
def download_page(request: Request, cid: int):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    total = cached_count(conn, **_effective_filters(conn, category))
    resp = templates.TemplateResponse("download.html", ctx(
        conn, request, category=category, total=total, fields=_DOWNLOAD_FIELDS))
    conn.close()
    return resp


@app.get("/categories/{cid}/export")
def export_category(request: Request, cid: int, format: str = "csv",
                    fields: List[str] = Query(default=[])):
    """Build the chosen-format, chosen-field export. Sync route -> runs in a
    threadpool so a large export doesn't block the event loop."""
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    keys, rows = shortlist.export_rows(conn, fields, **_effective_filters(conn, category))
    conn.close()
    labels = [shortlist.EXPORT_FIELDS[k][1] for k in keys]
    name = category.get("slug") or f"category-{cid}"

    if format == "json":
        payload = [{k: r.get(k) for k in keys} for r in rows]
        return Response(json.dumps(payload, indent=2, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if format == "xlsx":
        try:
            import openpyxl
        except Exception:
            return JSONResponse({"error": "Excel export needs openpyxl (pip install -r requirements.txt)."},
                                status_code=500)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "shortlist"
        ws.append(labels)
        for r in rows:
            ws.append([r.get(k) for k in keys])
        bio = io.BytesIO()
        wb.save(bio)
        return Response(bio.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    # default CSV
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(labels)
    for r in rows:
        w.writerow([r.get(k) for k in keys])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


# ---- funnel audit (per-page log) ----------------------------------------
def _build_audit(cid: int, filters: dict) -> dict:
    conn = connect()
    try:
        return audit.build_audit(conn, cid, filters["organisations"],
                                 filters["document_types"], filters["keywords"])
    finally:
        conn.close()


@app.post("/api/categories/{cid}/audit")
async def api_build_audit(request: Request, cid: int):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    filters = _effective_filters(conn, category)
    conn.close()
    if not filters["organisations"]:
        return JSONResponse({"error": "Set at least one organisation — the audit starts "
                             "at the organisation filter."}, status_code=400)
    counters = await run_in_threadpool(_build_audit, cid, filters)
    summary = [{"outcome": o, "count": n} for o, n in
               [(audit.OUTCOME_INCLUDED, counters[audit.OUTCOME_INCLUDED]),
                (audit.OUTCOME_DOCTYPE, counters[audit.OUTCOME_DOCTYPE]),
                (audit.OUTCOME_KEYWORD, counters[audit.OUTCOME_KEYWORD])]]
    return JSONResponse({"total": counters["total"], "summary": summary})


@app.get("/categories/{cid}/audit/download")
def download_audit(request: Request, cid: int, outcome: str = ""):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    rows = audit.audit_rows(conn, cid, outcome=outcome or None)
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["url", "outcome"])
    for r in rows:
        w.writerow([r["url"], r["outcome"]])
    tag = (outcome or "all").replace(":", "").replace(" ", "-")
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="audit-{cid}-{tag}.csv"'})


@app.get("/assistant", response_class=HTMLResponse)
def assistant_page(request: Request):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    cfg = _ai_config(conn)
    resp = templates.TemplateResponse("assistant.html", ctx(
        conn, request, active_nav="assistant", model=cfg["model"],
        provider_label=cfg["label"], has_key=cfg["has_key"],
        base_url=cfg["base_url"] or "api.anthropic.com (Claude default)"))
    conn.close()
    return resp


@app.post("/api/assistant")
async def api_assistant(request: Request):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    form = await request.form()
    prompt = (form.get("prompt") or "").strip()
    system = (form.get("system") or "").strip()
    if not prompt:
        return JSONResponse({"error": "Enter a prompt."}, status_code=400)
    conn = connect()
    cfg = _ai_config(conn)
    budget = _budget(conn)
    spent = _daily_spend(conn)
    conn.close()
    if budget > 0 and spent >= budget:
        return JSONResponse({"error": f"Daily AI budget of ${budget:.2f} reached "
                             f"(${spent:.4f} spent today). Raise it in Settings."})
    # Run the blocking SDK call off the event loop so one slow request can't stall the app.
    result = await run_in_threadpool(_ai_reply, cfg, system, prompt)
    if not result.get("error"):
        conn = connect()
        _log_ai_usage(conn, result.get("cost_usd"), result.get("input_tokens"),
                      result.get("output_tokens"), "assistant")
        conn.close()
    return JSONResponse(result)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    ai_models.seed_defaults(conn)
    models = ai_models.list_models(conn)
    active = settings.get_setting(conn, "active_model_id", "")
    if not active and models:
        active = str(models[0]["id"])
    for m in models:                      # is this model's provider usable?
        m["has_key"] = _provider_key(m["provider"]) is not None
        m["active"] = str(m["id"]) == str(active)
    resp = templates.TemplateResponse("settings.html", ctx(
        conn, request, active_nav="settings", models=models,
        providers=list(PROVIDERS.keys()),
        provider_keys={k: _provider_key(k) is not None for k in PROVIDERS},
        daily_budget=_budget(conn), max_docs=_max_docs(conn),
        spent_today=round(_daily_spend(conn), 4), saved=saved))
    conn.close()
    return resp


@app.post("/settings")
async def save_settings(request: Request):
    if not authed(request):
        return login_redirect(request)
    form = await request.form()
    conn = connect()
    active = form.get("active_model_id")
    if active:
        settings.set_setting(conn, "active_model_id", active)
    # Save edits to existing model rows.
    for m in ai_models.list_models(conn):
        rid = m["id"]
        prov = (form.get(f"m_{rid}_provider") or "").strip()
        mod = (form.get(f"m_{rid}_model_id") or "").strip()
        if prov in PROVIDERS and mod:
            try:
                ai_models.update_model(conn, rid, prov, mod,
                                       float(form.get(f"m_{rid}_input") or 0),
                                       float(form.get(f"m_{rid}_output") or 0))
            except ValueError:
                pass
    try:
        settings.set_setting(conn, "ai_daily_budget", str(float(form.get("ai_daily_budget") or DEFAULT_DAILY_BUDGET)))
    except ValueError:
        pass
    try:
        settings.set_setting(conn, "ai_max_docs_per_run", str(int(float(form.get("ai_max_docs_per_run") or DEFAULT_MAX_DOCS))))
    except ValueError:
        pass
    conn.close()
    return RedirectResponse(url=str(request.url_for("settings_page")) + "?saved=1", status_code=303)


@app.post("/settings/models/add")
async def add_model_route(request: Request):
    if not authed(request):
        return login_redirect(request)
    form = await request.form()
    provider = (form.get("provider") or "").strip()
    model_id = (form.get("model_id") or "").strip()
    conn = connect()
    if provider in PROVIDERS and model_id:
        try:
            ai_models.add_model(conn, provider, model_id,
                                float(form.get("input_per_m") or 0), float(form.get("output_per_m") or 0))
        except ValueError:
            pass
    conn.close()
    return RedirectResponse(url=str(request.url_for("settings_page")), status_code=303)


@app.post("/settings/models/delete")
async def delete_model_route(request: Request):
    if not authed(request):
        return login_redirect(request)
    form = await request.form()
    mid = form.get("model_id")
    conn = connect()
    if mid:
        try:
            ai_models.delete_model(conn, int(mid))
        except (TypeError, ValueError):
            pass
    conn.close()
    return RedirectResponse(url=str(request.url_for("settings_page")), status_code=303)


@app.on_event("startup")
def _startup():
    conn = connect()
    db.init_db(conn)
    conn.close()
