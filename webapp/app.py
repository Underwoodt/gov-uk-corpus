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
from govuk_corpus import (audit_stats, category_interview, evaluate, orgs,
                          peak_schedule, pricing, settings, shortlist)
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
# Per-connection Postgres statement timeout (ms). A query that exceeds it errors
# (the funnel shows "error") rather than hanging. Configurable so it can be raised
# on a slow box without a code change; 0 disables the timeout entirely.
try:
    DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "15000"))
except ValueError:
    DB_STATEMENT_TIMEOUT_MS = 15000


def connect():
    if not _is_pg():
        return db.connect(DB_PATH)
    return db.connect(DB_PATH, statement_timeout_ms=DB_STATEMENT_TIMEOUT_MS or None)


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
    spent = _daily_spend(conn)
    budget = _budget(conn)
    pct = round(spent / budget * 100, 1) if budget > 0 else None
    base = {"request": request, "corpus_meta": corpus_meta(conn), "active_nav": "categories",
            "budget_bar": {"spent": round(spent, 4), "budget": budget, "pct": pct}}
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


# Phase → settings key holding the chosen model id for that phase.
PHASE_MODEL_KEYS = {
    evaluate.PHASE_INCLUSION: "phase_model_inclusion",
    evaluate.PHASE_EXCLUSION: "phase_model_exclusion",
    evaluate.PHASE_ADJUDICATION: "phase_model_adjudication",
}


def _config_from_model(conn, m: dict) -> dict:
    """Build an AI config dict from an ai_models row."""
    provider = m["provider"]
    p = PROVIDERS.get(provider) or PROVIDERS[DEFAULT_PROVIDER]
    return {"provider": provider, "label": p["label"], "base_url": p["base_url"],
            "model": m["model_id"], "key": _provider_key(provider),
            "has_key": _provider_key(provider) is not None,
            "price_in": m["input_per_m"], "price_out": m["output_per_m"],
            "grid": dict(m), "peak_bitmap": peak_schedule.get_bitmap(conn, provider)}


def _default_config(conn) -> dict:
    p = PROVIDERS[DEFAULT_PROVIDER]
    return {"provider": DEFAULT_PROVIDER, "label": p["label"], "base_url": p["base_url"],
            "model": p["model"], "key": _provider_key(DEFAULT_PROVIDER),
            "has_key": _provider_key(DEFAULT_PROVIDER) is not None,
            "price_in": p["price_in"], "price_out": p["price_out"]}


def _ai_config(conn) -> dict:
    """Resolve the active AI model (from the ai_models table) + provider env."""
    ai_models.seed_defaults(conn)
    active = settings.get_setting(conn, "active_model_id", "")
    m = ai_models.get_model(conn, active) if active else None
    if not m:
        models = ai_models.list_models(conn)
        m = models[0] if models else None
    return _config_from_model(conn, m) if m else _default_config(conn)


def _ai_config_for_phase(conn, phase: str) -> dict:
    """Resolve the model configured for a phase (Settings → Model per phase), falling
    back to the active model when the phase has no specific model set."""
    ai_models.seed_defaults(conn)
    mid = settings.get_setting(conn, PHASE_MODEL_KEYS.get(phase, ""), "")
    m = ai_models.get_model(conn, mid) if mid else None
    return _config_from_model(conn, m) if m else _ai_config(conn)


def _ai_reply(config: dict, system: str, prompt: str) -> dict:
    """Single-turn convenience wrapper over _ai_chat."""
    return _ai_chat(config, system, [{"role": "user", "content": prompt}])


def _ai_chat(config: dict, system: str, messages: list, max_tokens: int = 1024) -> dict:
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
        kwargs = dict(model=config["model"], max_tokens=max_tokens, messages=list(messages))
        if system.strip():
            kwargs["system"] = system.strip()
        msg = client.messages.create(**kwargs)
        text = "".join(getattr(b, "text", "") for b in msg.content)
        actual_model = getattr(msg, "model", None)   # what the API actually served
        usage = getattr(msg, "usage", None)
        u = pricing.usage_breakdown(usage)   # {in_total, hit, miss, out} across providers
        # Peak or off-peak for this supplier at the moment the call ran (UTC).
        peak = pricing.is_peak_now(config.get("peak_bitmap"))
        in_tok = u["in_total"] if u else None
        out_tok = u["out"] if u else None
        cache_hit = u["hit"] if u else 0
        cache_miss = u["miss"] if u else 0
        cost = None
        if u is not None:
            grid = config.get("grid")
            if grid:   # tiered price grid × peak schedule: miss tokens at miss rate, hit at hit rate
                cost = pricing.call_cost(grid, peak, u["miss"], u["out"], u["hit"])
            if cost is None:   # fall back to the flat standard rate on the total input
                cost = round((in_tok / 1e6) * config["price_in"]
                             + (out_tok / 1e6) * config["price_out"], 6)
        return {"reply": text, "model": config["model"], "actual_model": actual_model,
                "provider": config["provider"], "peak": peak,
                "cache_hit_tokens": cache_hit, "cache_miss_tokens": cache_miss,
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


# ---- category assistant (guided interview -> pre-fill the create form) ---
@app.get("/categories/new/assistant", response_class=HTMLResponse)
def category_assistant_page(request: Request):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    resp = templates.TemplateResponse("category_assistant.html", ctx(
        conn, request, greeting=category_interview.GREETING))
    conn.close()
    return resp


@app.post("/api/categories/assistant")
async def api_category_assistant(request: Request):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    body = await request.json()
    messages = body.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return JSONResponse({"error": "no messages"}, status_code=400)
    # Keep only role/content and cap history length to bound cost.
    clean = [{"role": m.get("role"), "content": str(m.get("content") or "")}
             for m in messages[-24:] if m.get("role") in ("user", "assistant")]
    conn = connect()
    try:
        budget = _budget(conn)
        spent = _daily_spend(conn)
        if budget > 0 and spent >= budget:
            return JSONResponse({"error": f"Daily AI budget of ${budget:.2f} reached "
                                 f"(${spent:.4f} spent today)."}, status_code=429)
        cfg = _ai_config(conn)
        res = await run_in_threadpool(_ai_chat, cfg, category_interview.SYSTEM_PROMPT, clean)
        if res.get("error"):
            return JSONResponse({"error": res["error"]}, status_code=502)
        _log_ai_usage(conn, res.get("cost_usd"), res.get("input_tokens"),
                      res.get("output_tokens"), "assistant")
        reply = res.get("reply", "")
        fields = category_interview.parse_fields(reply)
        return JSONResponse({"reply": reply, "fields": fields})
    finally:
        conn.close()


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


# ---- persistent, version-keyed funnel cache -----------------------------
# Funnel counts are deterministic given (definition, corpus), so they can be cached
# and re-served without a query until the definition or the corpus changes.
def _def_version(category: dict) -> str:
    """A hash of the funnel-relevant fields — changes only when the filters change
    (editing the AI context or owner doesn't invalidate)."""
    parts = "|".join([
        category.get("dept_slugs") or "", category.get("document_type_slugs") or "",
        category.get("keywords") or "", str(category.get("include_child_orgs") or "")])
    return hashlib.md5(parts.encode("utf-8")).hexdigest()[:12]


def _corpus_version(conn) -> str:
    """A cheap token that changes when the corpus is (re)crawled. The crawler stamps
    runs.finished_at on each import; org-hierarchy changes ride along with it."""
    try:
        row = conn.execute("SELECT COALESCE(MAX(finished_at), '') AS v FROM runs").fetchone()
        return str(row["v"] or "")
    except Exception:
        return ""


def _funnel_cache_read(conn, cid: int, def_v: str, corpus_v: str) -> dict:
    """The cached stage counts for this category, or {} if stale/absent."""
    raw = settings.get_setting(conn, f"funnel_cache_{cid}", "")
    if not raw:
        return {}
    try:
        blob = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if blob.get("def") == def_v and blob.get("corpus") == corpus_v:
        return blob.get("stages") or {}
    return {}


def _funnel_cache_write(conn, cid: int, def_v: str, corpus_v: str, stage: str, count) -> None:
    raw = settings.get_setting(conn, f"funnel_cache_{cid}", "")
    blob = {}
    try:
        blob = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        blob = {}
    if blob.get("def") != def_v or blob.get("corpus") != corpus_v:
        blob = {"def": def_v, "corpus": corpus_v, "stages": {}}   # versions moved on -> reset
    blob.setdefault("stages", {})[stage] = count
    settings.set_setting(conn, f"funnel_cache_{cid}", json.dumps(blob))


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
    # Serve from the persistent cache unless the definition or corpus has changed.
    def_v, corpus_v = _def_version(category), _corpus_version(conn)
    cached = _funnel_cache_read(conn, cid, def_v, corpus_v)
    if stage in cached:
        conn.close()
        return JSONResponse({"stage": stage, "label": label, "count": cached[stage], "cached": True})
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
    _funnel_cache_write(conn, cid, def_v, corpus_v, stage, n)
    conn.close()
    return JSONResponse({"stage": stage, "label": label, "count": n, "cached": False})


@app.post("/api/categories/{cid}/funnel/refresh")
def api_funnel_refresh(request: Request, cid: int):
    """Clear this category's cached funnel counts (and the in-memory count/total caches)
    so the next funnel load re-queries from scratch."""
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    if not cat.get_category(conn, cid):
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    settings.set_setting(conn, f"funnel_cache_{cid}", "")   # persistent funnel cache
    conn.close()
    with _COUNT_LOCK:
        _COUNT_CACHE.clear()   # in-memory TTL counts (funnel + org/keyword breakdowns)
    _META_CACHE.clear()        # cached corpus total ("All pages")
    return JSONResponse({"ok": True})


@app.get("/categories/{cid}/audit-dashboard", response_class=HTMLResponse)
def audit_dashboard_page(request: Request, cid: int):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    stages = [(s, _FUNNEL_STAGES[s][0]) for s in ("all", "org", "doctype", "keyword")]
    resp = templates.TemplateResponse("audit_dashboard.html", ctx(
        conn, request, category=category, stages=stages))
    conn.close()
    return resp


@app.get("/api/categories/{cid}/audit-stats")
def api_audit_stats(request: Request, cid: int, stage: str = "keyword"):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    if stage not in _FUNNEL_STAGES:
        return JSONResponse({"error": "unknown stage"}, status_code=404)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    filters = _effective_filters(conn, category)
    applies = _FUNNEL_STAGES[stage][1]     # which filters this stage applies
    kw = {k: filters[k] for k in applies}
    kw.setdefault("match", "any")
    try:
        out = audit_stats.stats(conn, **kw)
    except Exception as e:
        conn.close()
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    conn.close()
    return JSONResponse(out)


@app.get("/api/categories/{cid}/org-breakdown")
def api_org_breakdown(request: Request, cid: int):
    """Pages contributed by EACH organisation, within the document-type + keyword filters
    — the 'which organisations matched' breakdown. Organisations overlap (a page can have
    several), so they don't sum to the shortlist total."""
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    filters = _effective_filters(conn, category)
    try:
        orgs_out = shortlist.org_breakdown(
            conn, organisations=filters["organisations"],
            document_types=filters["document_types"], keywords=filters["keywords"], match="any")
    except Exception as e:
        conn.close()
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    conn.close()
    return JSONResponse({"orgs": orgs_out})


@app.get("/api/categories/{cid}/keyword-breakdown")
def api_keyword_breakdown(request: Request, cid: int):
    """Pages matching EACH keyword individually, within the org + document-type set —
    the 'which terms matched' breakdown. Counts are independent, so they overlap and
    do not sum to the keyword-stage total (a page can match several terms)."""
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    filters = _effective_filters(conn, category)
    base = {"organisations": filters["organisations"], "document_types": filters["document_types"]}
    terms = []
    for kw in filters["keywords"]:
        try:
            n = cached_count(conn, keywords=[kw], match="any", **base)
        except Exception:
            n = None            # a single slow/failed term shouldn't sink the whole panel
        terms.append({"keyword": kw, "count": n})
    conn.close()
    # Sort by count desc (None last), preserving order for ties.
    terms.sort(key=lambda t: (t["count"] is None, -(t["count"] or 0)))
    return JSONResponse({"terms": terms})


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
            "price_in": price_in, "price_out": price_out,
            "grid": dict(row) if row else None,
            "peak_bitmap": peak_schedule.get_bitmap(conn, provider)}


def _active_run(conn, cid: int) -> str:
    return settings.get_setting(conn, f"active_run_{cid}", "") or ""


def _ensure_run(conn, cid: int) -> str:
    """Return the active run for the category, creating one (current provider/model) if none."""
    run_id = _active_run(conn, cid)
    if run_id and evaluate.get_run(conn, run_id):
        return run_id
    cfg = _ai_config_for_phase(conn, evaluate.PHASE_INCLUSION)
    run_id = evaluate.create_run(conn, cid, cfg["model"], cfg["provider"],
                                 phase=evaluate.PHASE_INCLUSION)
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
        phase = run.get("phase") or evaluate.PHASE_INCLUSION
        cfg = _cfg_for(conn, run["provider"], run["model"])
        if not cfg["key"]:
            return {"error": f"No API key for {cfg['label']} (this run's provider) — set one in Settings."}

        limit = min(limit, _max_docs(conn))
        filters = _effective_filters(conn, category)
        inclusion = category.get("inclusion_context") or ""
        exclusion = category.get("exclusion_context") or ""
        name = cat.prettify(category.get("slug")) or (category.get("description") or "the topic")

        is_exclusion = phase == evaluate.PHASE_EXCLUSION
        if is_exclusion:
            rows = evaluate.exclusion_candidates(conn, run_id, run.get("source_run_id"), limit)
        else:
            rows = evaluate.run_candidates(conn, run_id, cid, limit, **filters)
        done = 0
        cost = 0.0
        stopped = None
        for r in rows:
            if budget > 0 and spent >= budget:
                stopped = "budget"
                break
            t0 = time.time()
            if is_exclusion:
                prompt = evaluate.build_exclusion_prompt(
                    name, inclusion, exclusion,
                    category.get("adjudication_hints_keep") or "",
                    category.get("adjudication_hints_drop") or "",
                    r["title"], r["body"], r.get("pass1_reason") or "")
            else:
                prompt = evaluate.build_prompt(inclusion, exclusion, r["title"], r["description"], r["body"])
            res = _ai_reply(cfg, "", prompt)
            ms = int((time.time() - t0) * 1000)
            if res.get("error"):
                return {"error": res["error"], "run_id": run_id, "evaluated_this_run": done,
                        "cost_usd": round(cost, 6), "spent_today": round(spent, 4), "budget": budget}
            decision = (evaluate.parse_exclusion(res.get("reply", "")) if is_exclusion
                        else evaluate.parse_decision(res.get("reply", "")))
            evaluate.save_page(conn, run_id, cid, r["url"], decision, ms)
            evaluate.set_actual_model(conn, run_id, res.get("actual_model"))
            c = res.get("cost_usd") or 0.0
            evaluate.add_run_cost(conn, run_id, c, res.get("input_tokens"), res.get("output_tokens"),
                                  res.get("cache_hit_tokens"), res.get("cache_miss_tokens"))
            _log_ai_usage(conn, c, res.get("input_tokens"), res.get("output_tokens"), "evaluate")
            done += 1
            cost += c
            spent += c
        run = evaluate.get_run(conn, run_id)
        if is_exclusion:
            total = evaluate.kept_count(conn, run.get("source_run_id")) if run.get("source_run_id") else 0
        else:
            total = cached_count(conn, **filters)
        remaining = max(0, total - (run["pages"] or 0))
        advanced = None
        if remaining == 0 and stopped is None:
            evaluate.finish_run(conn, run_id)
            # Auto-advance: when an inclusion run completes, start Phase 2 (Exclusion)
            # over the pages it kept, on the exclusion phase's model.
            if not is_exclusion:
                keeps = evaluate.kept_count(conn, run_id)
                if keeps > 0:
                    excfg = _ai_config_for_phase(conn, evaluate.PHASE_EXCLUSION)
                    new_id = evaluate.create_run(conn, cid, excfg["model"], excfg["provider"],
                                                 phase=evaluate.PHASE_EXCLUSION, source_run_id=run_id)
                    settings.set_setting(conn, f"active_run_{cid}", new_id)
                    advanced = {"run_id": new_id, "phase": evaluate.PHASE_EXCLUSION, "remaining": keeps}
                    remaining = keeps
        warning = (f"Within 10% of the ${budget:.2f} daily budget (${spent:.4f} spent today)."
                   if budget > 0 and spent >= 0.9 * budget else None)
        return {"run_id": run_id, "model": run["model"], "provider": run["provider"],
                "phase": phase, "evaluated_this_run": done, "cost_usd": round(cost, 6),
                "spent_today": round(spent, 4), "budget": budget, "advanced": advanced,
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
    cfg = _ai_config_for_phase(conn, evaluate.PHASE_INCLUSION)   # new manual run = a fresh inclusion run
    run_id = evaluate.create_run(conn, cid, cfg["model"], cfg["provider"],
                                 phase=evaluate.PHASE_INCLUSION)
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


@app.post("/api/categories/{cid}/runs/{run_id}/rename")
async def api_rename_run(request: Request, cid: int, run_id: str):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "empty name"}, status_code=400)
    conn = connect()
    try:
        run = evaluate.get_run(conn, run_id)
        if not run or str(run["category_id"]) != str(cid):
            return JSONResponse({"error": "not found"}, status_code=404)
        evaluate.rename_run(conn, run_id, name[:120])
        return JSONResponse({"ok": True, "name": name[:120]})
    finally:
        conn.close()


@app.post("/api/categories/{cid}/runs/{run_id}/delete")
def api_delete_run(request: Request, cid: int, run_id: str):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    try:
        run = evaluate.get_run(conn, run_id)
        if not run or str(run["category_id"]) != str(cid):
            return JSONResponse({"error": "not found"}, status_code=404)
        evaluate.delete_run(conn, run_id)
        # If this was the category's active run, forget it so a fresh one starts next time.
        if _active_run(conn, cid) == run_id:
            settings.set_setting(conn, f"active_run_{cid}", "")
        return JSONResponse({"ok": True})
    finally:
        conn.close()


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
# Grouped into sections for the download page. Each field: (key, label, default_on,
# disabled, warning). 'url' is mandatory (disabled + on).
_DOWNLOAD_SECTIONS = [
    ("Content", [
        ("url", "URL", True, True, None),
        ("title", "Title", True, False, None),
        ("document_type", "Document type", True, False, None),
        ("parent_document_type", "Parent document type", False, False,
         "for html_publication pages: the parent publication's type"),
        ("content", "Content (raw JSON)", False, False, "warning: may make the download very large"),
    ]),
    ("Ownership", [
        ("organisations", "Organisations", False, False, "all linked organisation slugs"),
        ("primary_org", "Primary publishing organisation", False, False, None),
    ]),
    ("Freshness", [
        ("first_published_at", "First published at", False, False, None),
        ("public_updated_at", "Public updated at", False, False, None),
    ]),
    ("Quality attributes", [
        ("size", "Size", True, False, None),
        ("readability", "Readability score", False, False, None),
        ("gds_issues", "GDS number of issues", False, False, None),
        ("gds_findings", "GDS issues text", False, False, None),
    ]),
]


# ---- results table (browse the shortlist with configurable columns) -----
# (key, label, default_on). 'content' is intentionally excluded (too big to show).
_RESULTS_SECTIONS = [
    ("Content", [
        ("title", "Title", True),                        # rendered hyperlinked to URL
        ("effective_document_type", "Document type", True),   # html_publication -> parent type
        ("url", "URL", False),
    ]),
    ("Ownership", [
        ("primary_org", "Primary publishing organisation", True),
        ("organisations", "Organisations", False),
    ]),
    ("Freshness", [
        ("public_updated_at", "Last update", True),
        ("last_update_band", "Last update band", True),
        ("first_published_at", "First published at", False),
    ]),
    ("Quality attributes", [
        ("readability", "Readability age", True),
        ("gds_issues", "GDS issue count", True),
        ("gds_findings", "GDS issues text", False),
        ("size", "Size", False),
    ]),
]
# All fields the results endpoint fetches (url always, for the Title link).
_RESULTS_FIELDS = ["url"] + [k for _, fs in _RESULTS_SECTIONS for k, _, _ in fs if k != "url"]


@app.get("/categories/{cid}/results", response_class=HTMLResponse)
def results_table_page(request: Request, cid: int):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    resp = templates.TemplateResponse("results_table.html", ctx(
        conn, request, category=category, sections=_RESULTS_SECTIONS))
    conn.close()
    return resp


@app.get("/api/categories/{cid}/results-table")
def api_results_table(request: Request, cid: int, limit: int = 50, offset: int = 0, q: str = ""):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    filters = _effective_filters(conn, category)
    q = (q or "").strip()
    extra = {}
    if q:
        # Title contains `q`, over the WHOLE shortlist (not just the page). The wildcards
        # live in the parameter value, so there is no literal '%' in the SQL text.
        extra = {"extra_where": f"LOWER(c.title) LIKE LOWER({shortlist._P})",
                 "extra_params": ["%" + q + "%"]}
    try:
        # Fetch the page of rows first — the important part. A single page is cheap even
        # when the whole-shortlist count is slow.
        _keys, rows = shortlist.export_rows(conn, _RESULTS_FIELDS, limit=limit, offset=offset,
                                            **filters, **extra)
    except Exception as e:
        conn.close()
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    try:
        # A title search bypasses the count cache (its key ignores extra_where).
        total = (shortlist.count(conn, **filters, **extra) if q else cached_count(conn, **filters))
    except Exception:
        total = None
    conn.close()
    return JSONResponse({"total": total, "rows": rows, "limit": limit, "offset": offset, "q": q})


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
        conn, request, category=category, total=total, sections=_DOWNLOAD_SECTIONS))
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
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    # Built on demand (there is no separate build step) so the file always reflects the
    # current definition. Requires an organisation — the audit starts at the org filter.
    filters = _effective_filters(conn, category)
    if filters["organisations"]:
        audit.build_audit(conn, cid, filters["organisations"],
                          filters["document_types"], filters["keywords"])
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
    phase_models = [
        {"phase": ph, "key": PHASE_MODEL_KEYS[ph],
         "current": settings.get_setting(conn, PHASE_MODEL_KEYS[ph], "")}
        for ph in (evaluate.PHASE_INCLUSION, evaluate.PHASE_EXCLUSION, evaluate.PHASE_ADJUDICATION)]
    resp = templates.TemplateResponse("settings.html", ctx(
        conn, request, active_nav="settings", models=models,
        providers=list(PROVIDERS.keys()), phase_models=phase_models,
        provider_keys={k: _provider_key(k) is not None for k in PROVIDERS},
        daily_budget=_budget(conn), max_docs=_max_docs(conn),
        spent_today=round(_daily_spend(conn), 4), saved=saved))
    conn.close()
    return resp


def _price_form(form, prefix: str) -> dict:
    """Pull the six tiered price fields (prefix + field name) out of a form."""
    out = {}
    for f in ai_models.PRICE_FIELDS:
        v = form.get(prefix + f)
        if v not in (None, ""):
            try:
                out[f] = float(v)
            except (TypeError, ValueError):
                pass
    return out


@app.post("/settings")
async def save_settings(request: Request):
    if not authed(request):
        return login_redirect(request)
    form = await request.form()
    conn = connect()
    active = form.get("active_model_id")
    if active:
        settings.set_setting(conn, "active_model_id", active)
    # Per-phase model selection (Inclusion / Exclusion / Adjudication).
    for ph, key in PHASE_MODEL_KEYS.items():
        if key in form:
            settings.set_setting(conn, key, (form.get(key) or "").strip())
    # Save edits to existing model rows.
    for m in ai_models.list_models(conn):
        rid = m["id"]
        prov = (form.get(f"m_{rid}_provider") or "").strip()
        mod = (form.get(f"m_{rid}_model_id") or "").strip()
        if prov in PROVIDERS and mod:
            try:
                ai_models.update_model(conn, rid, prov, mod,
                                       prices=_price_form(form, f"m_{rid}_"))
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
                                prices=_price_form(form, "add_"))
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


@app.post("/api/models/{mid}/test")
async def test_model_route(request: Request, mid: int):
    """Ping a model with a tiny prompt to confirm the supplier accepts the name."""
    if not authed(request):
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
    conn = connect()
    row = ai_models.get_model(conn, mid)
    if not row:
        conn.close()
        return JSONResponse({"ok": False, "error": "Model not found."}, status_code=404)
    cfg = _cfg_for(conn, row["provider"], row["model_id"])
    conn.close()
    if not cfg.get("key"):
        return JSONResponse({"ok": False,
                             "error": f"No API key set for {cfg['label']}."})
    res = await run_in_threadpool(_ai_reply, cfg, "", "Reply with the single word: ok")
    if res.get("error"):
        return JSONResponse({"ok": False, "error": res["error"]})
    return JSONResponse({"ok": True, "actual_model": res.get("actual_model") or cfg["model"]})


@app.get("/settings/peak/{provider}", response_class=HTMLResponse)
def peak_schedule_page(request: Request, provider: str, saved: int = 0):
    if not authed(request):
        return login_redirect(request)
    if provider not in PROVIDERS:
        return RedirectResponse(url=str(request.url_for("settings_page")), status_code=303)
    conn = connect()
    resp = templates.TemplateResponse("peak_schedule.html", ctx(
        conn, request, active_nav="settings", provider=provider,
        provider_label=PROVIDERS[provider]["label"],
        days=peak_schedule.DAYS, hours=list(range(peak_schedule.HOURS)),
        grid=peak_schedule.get_grid(conn, provider),
        peak_count=peak_schedule.peak_hour_count(conn, provider), saved=saved))
    conn.close()
    return resp


@app.post("/settings/peak/{provider}")
async def save_peak_schedule(request: Request, provider: str):
    if not authed(request):
        return login_redirect(request)
    if provider not in PROVIDERS:
        return RedirectResponse(url=str(request.url_for("settings_page")), status_code=303)
    form = await request.form()
    grid = [[1 if form.get(f"h_{d}_{h}") else 0 for h in range(peak_schedule.HOURS)]
            for d in range(len(peak_schedule.DAYS))]
    conn = connect()
    peak_schedule.set_grid(conn, provider, grid)
    conn.close()
    return RedirectResponse(
        url=str(request.url_for("peak_schedule_page", provider=provider)) + "?saved=1",
        status_code=303)


@app.on_event("startup")
def _startup():
    conn = connect()
    db.init_db(conn)
    conn.close()
