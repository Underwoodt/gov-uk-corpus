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
import html
import io
import json
import logging
import os
import re
import threading
import secrets
import time
from typing import Dict, List, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from govuk_corpus import accounts, ai_models, audit
from govuk_corpus import categories as cat
from govuk_corpus import category_counts, guardrails, sessions
from govuk_corpus import (audit_stats, category_interview, evaluate, orgs,
                          peak_schedule, pricing, readability, roles, settings, shortlist)
from govuk_corpus.backend import db

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("CORPUS_DB", "data/pilot.db")
PASSWORD = os.getenv("DASHBOARD_PASSWORD")
COOKIE = "sb_auth"

# ---- auth mode (accounts programme, phase 2) ----------------------------
# "shared" = the existing single DASHBOARD_PASSWORD gate (default; live behaviour
# is unchanged). "accounts" = per-user session login (built behind this flag).
AUTH_MODE = os.getenv("AUTH_MODE", "shared").strip().lower()

# ---- CSRF (double-submit token) -----------------------------------------
CSRF_COOKIE = "sb_csrf"


def _csrf_token(request: Request) -> str:
    """This browser's CSRF token: the existing cookie, or a fresh one stashed on
    request.state for the middleware to set on the response."""
    tok = request.cookies.get(CSRF_COOKIE)
    if tok:
        return tok
    tok = getattr(request.state, "_csrf_new", None)
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.state._csrf_new = tok
    return tok


async def _csrf_guard(request: Request) -> None:
    """Reject state-changing requests without a matching CSRF token. Double-submit:
    the token must arrive as the X-CSRF-Token header (fetch) or a `csrf` form field
    and equal the sb_csrf cookie. Safe methods pass through."""
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    token = request.headers.get("x-csrf-token")
    if token is None and "form" in request.headers.get("content-type", "").lower():
        token = (await request.form()).get("csrf")
    if not cookie or not token or not hmac.compare_digest(str(cookie), str(token)):
        raise HTTPException(status_code=403, detail="CSRF check failed")


app = FastAPI(title="Shortlist Builder", dependencies=[Depends(_csrf_guard)])
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


# ---- security headers + CSRF cookie -------------------------------------
# Additive, safe headers on every response; HSTS is opt-in (ENABLE_HSTS=1, HTTPS
# only). CSP is intentionally NOT set yet (inline scripts/styles). The CSRF cookie
# is issued here when absent so forms/fetches always have a token to echo back.
@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if os.getenv("ENABLE_HSTS") == "1":
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if CSRF_COOKIE not in request.cookies:
        # Readable by JS (the fetch wrapper echoes it in a header) — not a secret on
        # its own; it only has to match the copy the browser also sends.
        resp.set_cookie(CSRF_COOKIE, _csrf_token(request), samesite="lax",
                        secure=_secure_cookies(), max_age=31536000, path="/")
    return resp


# ---- last-resort error page ---------------------------------------------
# Best practice: never surface a traceback, SQL, or stack detail to the user —
# it leaks internals and is a standard pentest finding. Instead we log the full
# traceback server-side under a short reference and show the user a plain page
# carrying only that reference, which they can quote when reporting the problem
# (an operator then greps the logs for `ref=<code>`). HTTPException (403/404/…)
# keeps Starlette's own handling — this only catches unhandled 500s.
_error_log = logging.getLogger("webapp.error")


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    ref = secrets.token_hex(4)
    _error_log.exception("unhandled error ref=%s method=%s path=%s",
                         ref, request.method, request.url.path)
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Something went wrong</title>
<link rel="stylesheet" href="/static/govuk.css"></head>
<body><div class="container" style="max-width:640px;margin:60px auto;padding:0 20px;">
<h1>Sorry, something went wrong</h1>
<p>The page couldn't be displayed because of a problem on our side. This has been
logged and we can look into it.</p>
<p>If you report this, please quote the reference below so we can find the exact
error in the logs:</p>
<p><strong>Reference:</strong> <code style="font-size:1.1em;">{ref}</code></p>
<p style="margin-top:28px;"><a href="/">Return to the start page</a></p>
</div></body></html>"""
    return HTMLResponse(body, status_code=500)

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
    user = current_user(request)
    # In accounts mode the effective role is the logged-in user's own role; in
    # shared mode it's the single global role set on Settings.
    role = user["role"] if (AUTH_MODE == "accounts" and user) else roles.get_role(conn)
    base = {"request": request, "corpus_meta": corpus_meta(conn), "active_nav": "categories",
            "budget_bar": {"spent": round(spent, 4), "budget": budget, "pct": pct},
            # Effective role + a gate helper for templates:
            #   {% if can_use('Administrator') %}…{% endif %}
            "role": role, "can_use": lambda required=None: roles.allows(role, required),
            # Accounts mode: the logged-in user (None in shared-password mode).
            "auth_mode": AUTH_MODE, "user": user,
            "csrf_token": _csrf_token(request)}
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
MAX_CONSEC_EVAL_ERRORS = 6      # consecutive AI errors before a run bails (provider likely down)


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

# Phase → settings key holding the execution mode (synchronous | batch) for that phase.
PHASE_MODE_KEYS = {
    evaluate.PHASE_INCLUSION: "phase_mode_inclusion",
    evaluate.PHASE_EXCLUSION: "phase_mode_exclusion",
    evaluate.PHASE_ADJUDICATION: "phase_mode_adjudication",
}


def _phase_mode(conn, phase: str) -> str:
    """The stored execution mode for a phase (defaults to synchronous)."""
    return evaluate.normalise_mode(settings.get_setting(conn, PHASE_MODE_KEYS.get(phase, ""), ""))


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
        return {"fatal": True, "error": f"No API key set for {config['label']}. Add its key to "
                f"~/gov-uk-corpus.env and restart, or pick a provider that has one in Settings."}
    try:
        import anthropic
    except Exception:
        return {"fatal": True,
                "error": "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"}
    try:
        # Retry transient errors (429 / 5xx / timeouts) at the SDK, honouring Retry-After,
        # so a single hiccup from a rate-limiting or slow provider self-heals instead of
        # aborting an evaluation run. Tune with AI_MAX_RETRIES / AI_TIMEOUT.
        client_kwargs = dict(api_key=config["key"], timeout=float(os.getenv("AI_TIMEOUT", "45")),
                             max_retries=int(os.getenv("AI_MAX_RETRIES", "4")))
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


def _secure_cookies() -> bool:
    """Send the Secure flag once we're on confirmed HTTPS (same signal as HSTS)."""
    return os.getenv("ENABLE_HSTS") == "1" or os.getenv("SECURE_COOKIES") == "1"


def _client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


# Simple in-process sliding-window rate limiter (per key). Fine for a single
# uvicorn worker; a shared/DB-backed limiter is a later hardening step.
_RATE: Dict[str, list] = {}


def _rate_limit(key: str, max_hits: int, window_s: int) -> bool:
    """Record a hit for `key`; return False if it exceeds max_hits in the window."""
    now = time.time()
    hits = [t for t in _RATE.get(key, []) if t > now - window_s]
    if len(hits) >= max_hits:
        _RATE[key] = hits
        return False
    hits.append(now)
    _RATE[key] = hits
    return True


def _set_session_cookie(resp, cookie: str) -> None:
    resp.set_cookie(sessions.COOKIE_NAME, cookie, httponly=True, samesite="lax",
                    secure=_secure_cookies(), max_age=sessions.SESSION_TTL_HOURS * 3600, path="/")


def current_user(request: Request) -> Optional[dict]:
    """The logged-in user (accounts mode) or None. Resolves the session cookie once
    per request and caches it on request.state."""
    if AUTH_MODE != "accounts":
        return None
    cached = getattr(request.state, "_user", "unset")
    if cached != "unset":
        return cached
    user = None
    cookie = request.cookies.get(sessions.COOKIE_NAME, "")
    if cookie:
        conn = connect()
        try:
            user = sessions.resolve(conn, cookie)
        except Exception:
            user = None
        finally:
            conn.close()
    request.state._user = user
    return user


def authed(request: Request) -> bool:
    if AUTH_MODE == "accounts":
        return current_user(request) is not None
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
_LOGIN_HEAD = ("""<!doctype html><meta charset=utf-8>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M4 1h5l3 3v11H4z' fill='%231d70b8'/><path d='M9 1v3h3z' fill='%23003078'/><g fill='none' stroke='%23fff' stroke-width='1' stroke-linecap='round'><path d='M6 7h5'/><path d='M6 9h5'/><path d='M6 11h4'/></g></svg>">
    <link rel=stylesheet href='/static/govuk.css'>
    <div class='masthead'><div class='wrap'><img class='brand-logo' src='/static/notgovuk.svg' alt='NOT.GOV.UK — Defra supplier, operated with GOV.UK API-supplied data'><span class='brand'>Content Shortlist Builder</span></div></div>
    <script>
    // Give every password field a show/hide eye toggle (auth pages only).
    document.addEventListener('DOMContentLoaded', function () {
      var EYE = "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z'/><circle cx='12' cy='12' r='3'/></svg>";
      var OFF = "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M17.94 17.94A10.07 10.07 0 0 1 12 20C5 20 1 12 1 12a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19'/><path d='M14.12 14.12a3 3 0 1 1-4.24-4.24'/><line x1='1' y1='1' x2='23' y2='23'/></svg>";
      document.querySelectorAll('input[type=password]').forEach(function (inp) {
        var wrap = document.createElement('span');
        wrap.className = 'pw-wrap';
        inp.parentNode.insertBefore(wrap, inp);
        wrap.appendChild(inp);
        var btn = document.createElement('button');
        btn.type = 'button'; btn.className = 'pw-toggle';
        btn.setAttribute('aria-label', 'Show password');
        btn.innerHTML = EYE;
        wrap.appendChild(btn);
        btn.addEventListener('click', function () {
          var show = inp.type === 'password';
          inp.type = show ? 'text' : 'password';
          btn.innerHTML = show ? OFF : EYE;
          btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
          inp.focus();
        });
      });
    });
    </script>""")


@app.get("/login", response_class=HTMLResponse)
def login(request: Request, bad: int = 0, locked: int = 0):
    err = ""
    if locked:
        err = f"<div class='error-summary'><h2>There is a problem</h2><ul><li>{accounts.LOGIN_LOCKED_MESSAGE}</li></ul></div>"
    elif bad:
        msg = accounts.LOGIN_FAILED_MESSAGE if AUTH_MODE == "accounts" else "Incorrect password."
        err = f"<div class='error-summary'><h2>There is a problem</h2><ul><li>{msg}</li></ul></div>"
    if AUTH_MODE == "accounts":
        fields = ("<div class='field'><label class='q' for='e'>Email address</label>"
                  "<input id='e' name='email' type='email' autocomplete='username'></div>"
                  "<div class='field'><label class='q' for='p'>Password</label>"
                  "<input id='p' name='password' type='password' autocomplete='current-password'></div>")
    else:
        fields = ("<div class='field'><label class='q' for='p'>Password</label>"
                  "<input id='p' name='password' type='password'></div>")
    reg = (f"<p style='margin-top:16px;'><a href='{request.url_for('register')}'>Create an account</a></p>"
           if AUTH_MODE == "accounts" else "")
    return HTMLResponse(
        f"""{_LOGIN_HEAD}
        <div class='wrap body'><h1>Sign in</h1>{err}
        <form class='authform' method=post action='{request.url_for('do_login')}'>
        <input type=hidden name=csrf value='{_csrf_token(request)}'>{fields}
        <button class='btn' type=submit>Sign in</button></form>{reg}</div>
        <div class='page-id'>guc-0014</div>""")


@app.post("/login")
def do_login(request: Request, password: str = Form(""), email: str = Form("")):
    if AUTH_MODE == "accounts":
        conn = connect()
        try:
            user, reason = accounts.authenticate(conn, email, password, ip=_client_ip(request))
            if user is None:
                where = "?locked=1" if reason == "locked" else "?bad=1"
                return RedirectResponse(url=str(request.url_for("login")) + where, status_code=303)
            cookie = sessions.create_session(conn, user["id"], ip=_client_ip(request),
                                             user_agent=request.headers.get("user-agent"))
        finally:
            conn.close()
        resp = RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
        _set_session_cookie(resp, cookie)
        return resp
    # shared-password mode
    if PASSWORD and not hmac.compare_digest(password, PASSWORD):
        return RedirectResponse(url=str(request.url_for("login")) + "?bad=1", status_code=303)
    resp = RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    token = hmac.new((PASSWORD or "").encode(), b"ok", hashlib.sha256).hexdigest()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=86400)
    return resp


@app.post("/logout")
def do_logout(request: Request):
    resp = RedirectResponse(url=str(request.url_for("login")), status_code=303)
    if AUTH_MODE == "accounts":
        cookie = request.cookies.get(sessions.COOKIE_NAME, "")
        if cookie:
            conn = connect()
            try:
                user = current_user(request)
                sessions.revoke(conn, cookie)
                if user:
                    accounts.audit(conn, "logout", user_id=user["id"], ip=_client_ip(request))
            finally:
                conn.close()
        resp.delete_cookie(sessions.COOKIE_NAME, path="/")
    else:
        resp.delete_cookie(COOKIE)
    return resp


# ---- registration (accounts mode only) ----------------------------------
def _register_page(request: Request, *, error: str = "", values: Optional[dict] = None) -> HTMLResponse:
    v = values or {}
    def field(k):
        return html.escape((v.get(k) or "").strip(), quote=True)
    err = (f"<div class='error-summary'><h2>There is a problem</h2><ul><li>{html.escape(error)}</li></ul></div>"
           if error else "")
    suggestion = accounts.suggest_passphrase()
    rules_li = "".join(f"<li>{html.escape(r)}</li>" for r in accounts.PASSWORD_RULES)
    return HTMLResponse(
        f"""{_LOGIN_HEAD}
        <div class='wrap body'><h1>Create an account</h1>{err}
        <p class='secondary'>Use your DEFRA or Equal Experts email address.</p>
        <form class='authform' method=post action='{request.url_for('do_register')}'>
        <input type=hidden name=csrf value='{_csrf_token(request)}'>
        <div class='field'><label class='q' for='fn'>First name</label>
        <input id='fn' name='first_name' type='text' autocomplete='given-name' value='{field("first_name")}'></div>
        <div class='field'><label class='q' for='ln'>Last name</label>
        <input id='ln' name='last_name' type='text' autocomplete='family-name' value='{field("last_name")}'></div>
        <div class='field'><label class='q' for='e'>Email address</label>
        <input id='e' name='email' type='email' autocomplete='username' value='{field("email")}'></div>
        <div class='pw-suggest'>
        Suggested password: <code id='pwsg'>{suggestion}</code>
        <button type='button' class='btn secondary-btn' style='padding:4px 10px;font-size:14px;margin-left:8px;'
          onclick="var p=document.getElementById('pwsg').textContent;document.getElementById('p').value=p;document.getElementById('p2').value=p;">Use this</button>
        <div class='secondary' style='margin-top:6px;'>Three random words — easy to remember, hard to guess. Or choose your own.</div></div>
        <div class='field'><label class='q' for='p'>Password</label>
        <input id='p' name='password' type='password' autocomplete='new-password'></div>
        <div class='field'><label class='q' for='p2'>Confirm password</label>
        <input id='p2' name='confirm' type='password' autocomplete='new-password'></div>
        <details><summary>Password rules</summary>
        <div class='detail'><ul style='margin:0;padding-left:20px;'>{rules_li}</ul></div></details>
        <button class='btn' type=submit>Create account</button></form>
        <p style='margin-top:16px;'><a href='{request.url_for('login')}'>Already have an account? Sign in</a></p></div>
        <div class='page-id'>guc-0015</div>""")


@app.get("/register", response_class=HTMLResponse)
def register(request: Request):
    if AUTH_MODE != "accounts":
        return RedirectResponse(url=str(request.url_for("login")), status_code=303)
    return _register_page(request)


@app.post("/register")
def do_register(request: Request, first_name: str = Form(""), last_name: str = Form(""),
                email: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    if AUTH_MODE != "accounts":
        return RedirectResponse(url=str(request.url_for("login")), status_code=303)
    vals = {"first_name": first_name, "last_name": last_name, "email": email}
    ip = _client_ip(request)
    if not _rate_limit(f"register:{ip}", 5, 3600):
        return _register_page(request, error="Too many attempts. Please try again later.", values=vals)
    if password != confirm:
        return _register_page(request, error="The passwords do not match.", values=vals)
    conn = connect()
    try:
        # role/account_status are NOT taken from the form (mass-assignment guard):
        # create_user defaults them to User / active.
        user = accounts.create_user(conn, email=email, first_name=first_name,
                                    last_name=last_name, password=password)
    except accounts.EmailTakenError:
        conn.close()
        return _register_page(request, error="An account with that email address already exists.", values=vals)
    except ValueError as e:
        conn.close()
        return _register_page(request, error=str(e), values=vals)
    accounts.audit(conn, "account_created", user_id=user["id"], ip=ip)
    cookie = sessions.create_session(conn, user["id"], ip=ip,
                                     user_agent=request.headers.get("user-agent"))
    conn.close()
    resp = RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    _set_session_cookie(resp, cookie)
    return resp


# ---- list ---------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def list_categories_page(request: Request, flash: str = ""):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    rows = cat.list_categories(conn)
    # Read the precomputed input-shortlist size per category (refreshed by the
    # nightly cycle's tidy-up and on every create/edit) in one query, instead of
    # running a live corpus COUNT per row on this hot page.
    counts = category_counts.get_counts(conn)
    for r in rows:
        r["display_name"] = r.get("slug") and cat.prettify(r["slug"]) or (r.get("description") or "Untitled")
        r["updated"] = (r.get("updated_at") or r.get("created_at") or "")[:10] or "—"
        hit = counts.get(int(r["id"]))
        r["pages_kept"] = "{:,}".format(hit["pages_kept"]) if hit and hit["pages_kept"] is not None else "—"
        r["pages_kept_at"] = (hit["computed_at"] or "")[:10] if hit else ""
    return templates.TemplateResponse("list.html", ctx(conn, request, categories=rows, flash=flash))


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
    cats = cat.list_categories(conn)
    categories = [{"id": c["id"],
                   "name": cat.prettify(c.get("slug")) or (c.get("description") or "Untitled")}
                  for c in cats]
    resp = templates.TemplateResponse("category_assistant.html", ctx(
        conn, request, greeting=category_interview.GREETING, categories=categories,
        main_doc_types=list(category_interview.MAIN_DOCUMENT_TYPES)))
    conn.close()
    return resp


@app.get("/api/categories/{cid}/definition")
def api_category_definition(request: Request, cid: int):
    """The category's current field values, for the assistant to import and refine."""
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    try:
        c = cat.get_category(conn, cid)
        if not c:
            return JSONResponse({"error": "not found"}, status_code=404)
        name = cat.prettify(c.get("slug")) or (c.get("description") or "Untitled")
        fields = {k: c.get(k) for k in category_interview.FIELD_KEYS
                  if c.get(k) is not None and c.get(k) != ""}
        if "include_child_orgs" in c:
            fields["include_child_orgs"] = bool(c.get("include_child_orgs"))
        return JSONResponse({"name": name, "fields": fields})
    finally:
        conn.close()


def _slug_reference(conn, text: str) -> str:
    """A note of REAL organisation / document-type slugs matching words in the user's
    latest message, appended to the interview prompt so the assistant recommends valid
    slugs (widely) rather than inventing them — and re-checks whenever a field changes."""
    org_matches = orgs.search(conn, text, limit=20)
    dt_matches = category_interview.match_document_types(text)
    if not org_matches and not dt_matches:
        return ""
    lines = ["SLUG REFERENCE — real slugs from the corpus that match words in the user's "
             "latest message. When the user names organisations or document types that are "
             "NOT exact slugs, recommend from these (widely — offer any that plausibly "
             "match) and never invent a slug. If a word matches none here, say so and offer "
             "the closest options."]
    if org_matches:
        lines.append("Organisation slugs: " + ", ".join(m["slug"] for m in org_matches))
    if dt_matches:
        lines.append("Document-type slugs: " + ", ".join(dt_matches))
    return "\n".join(lines)


@app.post("/api/categories/assistant")
async def api_category_assistant(request: Request):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    body = await request.json()
    messages = body.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return JSONResponse({"error": "no messages"}, status_code=400)
    edit_fields = body.get("edit_fields") if isinstance(body.get("edit_fields"), dict) else None
    # Keep only role/content and cap history length to bound cost.
    clean = [{"role": m.get("role"), "content": str(m.get("content") or "")}
             for m in messages[-24:] if m.get("role") in ("user", "assistant")]
    # Guardrail: block the user's latest message if it carries personal data or
    # prohibited language, before it reaches the model. Pause on the same question.
    last_user = next((m["content"] for m in reversed(clean) if m["role"] == "user"), "")
    finding = guardrails.check(last_user)
    if finding:
        return JSONResponse({"reply": guardrails.refusal_message(finding),
                             "fields": None, "suggestion": None})
    conn = connect()
    try:
        budget = _budget(conn)
        spent = _daily_spend(conn)
        if budget > 0 and spent >= budget:
            return JSONResponse({"error": f"Daily AI budget of ${budget:.2f} reached "
                                 f"(${spent:.4f} spent today)."}, status_code=429)
        cfg = _ai_config(conn)
        # Re-check the user's latest message against real slugs and hand the model the
        # matches, so a changed organisation/doc-type field gets valid-slug recommendations.
        system = category_interview.system_prompt(edit_fields)
        ref = _slug_reference(conn, last_user)
        if ref:
            system = system + "\n\n" + ref
        res = await run_in_threadpool(_ai_chat, cfg, system, clean)
        if res.get("error"):
            return JSONResponse({"error": res["error"]}, status_code=502)
        _log_ai_usage(conn, res.get("cost_usd"), res.get("input_tokens"),
                      res.get("output_tokens"), "assistant")
        reply = res.get("reply", "")
        fields = category_interview.parse_fields(reply)
        suggestion = category_interview.parse_suggestion(reply)
        return JSONResponse({"reply": reply, "fields": fields, "suggestion": suggestion})
    finally:
        conn.close()


def _slug_errors(conn, data: dict) -> list:
    """Server-side slug validation — the hard backstop behind the assistant's guidance.
    Reject organisation / document-type slugs that don't exist (plurals and typos won't),
    suggesting the closest real slugs. Returns human-readable errors ([] if all valid)."""
    errors: list = []
    known_dt = set(category_interview.DOCUMENT_TYPES)
    for s in cat.parse_list(data.get("document_type_slugs")):
        if s not in known_dt:
            near = category_interview.match_document_types(s)
            hint = f" Did you mean: {', '.join(near)}?" if near else ""
            errors.append(f"Unknown document type '{s}' — not a real slug.{hint}")
    for s in cat.parse_list(data.get("dept_slugs")):
        if orgs.get_org(conn, s) is None:
            near = [m["slug"] for m in orgs.search(conn, s, limit=5)]
            hint = f" Did you mean: {', '.join(near)}?" if near else ""
            errors.append(f"Unknown organisation '{s}' — not a real slug.{hint}")
    return errors


@app.post("/categories/new")
async def create_category(request: Request):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    form = await request.form()
    data = form_values(form)
    # The owner is whoever is logged in — take their email (accounts mode), not the form.
    cu = current_user(request)
    if cu and cu.get("email"):
        data["owner_email"] = cu["email"]
    errors = cat.validate(data) + _slug_errors(conn, data)
    if errors:
        return templates.TemplateResponse("form.html", _form_ctx(conn, request, None, data, errors))
    cid = cat.create_category(conn, data)
    category_counts.refresh_one(conn, cid)  # keep the list's stored count fresh
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
    errors = cat.validate(data) + _slug_errors(conn, data)
    if errors:
        merged = {**category, **data}
        return templates.TemplateResponse("form.html", _form_ctx(conn, request, category, merged, errors))
    cat.update_category(conn, cid, data)
    category_counts.refresh_one(conn, cid)  # keep the list's stored count fresh
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
    # SQL preview is cheap (no DB hit) — render the per-stage COUNT queries the funnel
    # runs, plus the shortlist SELECT, with comment separators.
    def _pretty(sql_, params_):
        return shortlist.pretty_sql(shortlist.interpolate_sql(sql_, params_))

    stage_specs = [
        ("All pages", {}),
        ("After organisation filter", {"organisations": filters["organisations"]}),
        ("After document-type filter",
         {"organisations": filters["organisations"], "document_types": filters["document_types"]}),
        ("After keyword filter",
         {"organisations": filters["organisations"], "document_types": filters["document_types"],
          "keywords": filters["keywords"], "match": "any"}),
    ]
    blocks = ["-- ===== Funnel counts: one COUNT(*) per stage (the numbers in the table above) ====="]
    for label, kw in stage_specs:
        cs, cp = shortlist.build_query(count_only=True, **kw)
        blocks.append(f"-- {label}\n{_pretty(cs, cp)};")
    sel_sql, sel_params = shortlist.build_query(include_title=True, limit=10000, **filters)
    blocks.append("-- ===== Shortlist rows: the pages returned (results table / download) =====\n"
                  + f"{_pretty(sel_sql, sel_params)};")
    pretty = "\n\n".join(blocks)
    eval_max_docs = _max_docs(conn)
    conn.close()
    return templates.TemplateResponse("preview.html", ctx(
        connect(), request, category=category, sql=pretty, stages=stages,
        eval_max_docs=eval_max_docs))


@app.get("/categories/{cid}/shortlist", response_class=HTMLResponse)
def shortlist_page(request: Request, cid: int, stage: str = "final", tab: str = "shortlist"):
    """Audit Results page — Shortlist and Dashboard sub-tabs. `stage` pre-selects
    the shortlist stage dropdown; `tab` picks the initial sub-tab (shortlist |
    dashboard). Data loads from /api/categories/{cid}/audit-shortlist and
    /api/categories/{cid}/audit-stats."""
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    if stage not in _AUDIT_STAGE_KEYS:
        stage = "final"
    stages = [(s, _FUNNEL_STAGES[s][0]) for s in ("all", "org", "doctype", "keyword")]  # dashboard levels
    gds_check_meta = [{"name": c.name, "weight": c.weight, "reason": c.reason}
                      for c in readability.CHECKS]
    return templates.TemplateResponse("audit_shortlist.html", ctx(
        conn, request, category=category, stage=stage,
        initial_tab=("dashboard" if tab == "dashboard" else "shortlist"), stages=stages,
        gds_check_meta=gds_check_meta,
        audit_stages=_AUDIT_STAGES, audit_sections=_DOWNLOAD_SECTIONS))


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
        skipped = 0
        consec_err = 0        # AI errors in a row -> likely a provider outage, so bail out
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
                # Fatal (no key / bad config) -> stop the run and report it.
                if res.get("fatal"):
                    return {"error": res["error"], "fatal": True, "run_id": run_id,
                            "evaluated_this_run": done, "skipped": skipped,
                            "cost_usd": round(cost, 6), "spent_today": round(spent, 4), "budget": budget}
                # Transient (the SDK has already retried): if it keeps failing the
                # provider is likely down — stop cleanly so we don't mark hundreds of
                # pages unscored; the user re-executes to resume where it left off.
                consec_err += 1
                if consec_err >= MAX_CONSEC_EVAL_ERRORS:
                    return {"error": f"Stopped after {consec_err} evaluation errors in a row "
                            f"(last: {res['error']}). The provider may be down or rate-limiting — "
                            f"re-execute to resume where it left off.",
                            "run_id": run_id, "evaluated_this_run": done, "skipped": skipped,
                            "cost_usd": round(cost, 6), "spent_today": round(spent, 4),
                            "budget": budget, "stopped": "errors"}
                # Isolated failure: record the page as unscored (so it's excluded next
                # time and shows on the Run detail 'Not parsed' list) and carry on.
                evaluate.save_page(conn, run_id, cid, r["url"],
                                   {"keep": None, "score": None,
                                    "reason": f"skipped after AI error: {str(res['error'])[:300]}"}, ms)
                skipped += 1
                done += 1
                continue
            consec_err = 0
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
                "phase": phase, "evaluated_this_run": done, "skipped": skipped,
                "cost_usd": round(cost, 6), "spent_today": round(spent, 4), "budget": budget,
                "advanced": advanced, "warning": warning, "stopped": stopped,
                "remaining": remaining, "run": run}
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


# ---- server-side background evaluation ----------------------------------
# A background thread drives _run_evaluation in chunks until the shortlist is
# exhausted (through the auto-advanced exclusion phase), the daily budget stops
# it, it's stopped, or it errors — so a run finishes without the browser open.
# Progress lives in-memory per category; it does NOT survive a service restart
# (the DB run does, and can be resumed from the page).
BG_EVAL_CHUNK = 25
_bg_evals: Dict[int, dict] = {}
_bg_lock = threading.Lock()


def _background_eval_loop(cid: int, stop_event: threading.Event, status: dict) -> None:
    try:
        conn = connect()
        try:
            cap = _max_docs(conn)          # pages-per-run limit (Settings), e.g. 600
            status["run_id"] = _active_run(conn, cid) or None   # so "In Progress" shows at once
        finally:
            conn.close()
        while not stop_event.is_set():
            res = _run_evaluation(cid, BG_EVAL_CHUNK)
            if res.get("error"):
                status["error"] = res["error"]
                logging.getLogger("assistant").warning("background eval stopped (cid=%s): %s", cid, res["error"])
                break
            status["done"] += res.get("evaluated_this_run", 0)
            status["skipped"] = status.get("skipped", 0) + res.get("skipped", 0)
            status["cost"] += res.get("cost_usd") or 0.0
            status["phase"] = res.get("phase")
            status["remaining"] = res.get("remaining")
            status["run_id"] = res.get("run_id")
            status["spent_today"] = res.get("spent_today")
            status["budget"] = res.get("budget")
            if res.get("stopped") == "budget":
                status["stopped"] = "budget"
                break
            # No auto-advance and nothing left (or nothing progressed) -> finished.
            if not res.get("advanced") and (res.get("evaluated_this_run", 0) == 0
                                            or res.get("remaining") == 0):
                break
            # Per-run page cap: evaluate up to `cap` NEW pages this execution, then
            # stop cleanly so we actually run the full 600 (not nothing) and the user
            # re-executes to continue where it left off, rather than doing the whole
            # shortlist in one unbounded run.
            if cap and status["done"] >= cap:
                status["stopped"] = "cap"
                break
    except Exception as e:                      # never let the thread die silently
        status["error"] = f"{type(e).__name__}: {e}"
    finally:
        status["running"] = False
        status["finished_at"] = db.now_iso()


@app.post("/api/categories/{cid}/evaluate-bg/start")
async def api_bg_eval_start(request: Request, cid: int):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    ok = cat.get_category(conn, cid) is not None
    conn.close()
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    with _bg_lock:
        ent = _bg_evals.get(cid)
        if ent and ent["status"].get("running"):
            return JSONResponse({"already_running": True, "status": ent["status"]})
        stop_event = threading.Event()
        status = {"running": True, "done": 0, "skipped": 0, "cost": 0.0, "phase": None,
                  "remaining": None, "run_id": None, "stopped": None, "error": None,
                  "started_at": db.now_iso(), "finished_at": None}
        t = threading.Thread(target=_background_eval_loop, args=(cid, stop_event, status), daemon=True)
        _bg_evals[cid] = {"thread": t, "stop": stop_event, "status": status}
        t.start()
    return JSONResponse({"started": True, "status": status})


@app.get("/api/categories/{cid}/evaluate-bg/status")
async def api_bg_eval_status(request: Request, cid: int):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    ent = _bg_evals.get(cid)
    return JSONResponse(ent["status"] if ent else {"running": False})


@app.post("/api/categories/{cid}/evaluate-bg/stop")
async def api_bg_eval_stop(request: Request, cid: int):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    ent = _bg_evals.get(cid)
    if ent:
        ent["stop"].set()
    return JSONResponse({"stopping": ent is not None})


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
    runs = evaluate.list_runs(conn, cid)
    category = cat.get_category(conn, cid)
    # Annotate each run with the total it evaluates toward, for the "X / N" display:
    # inclusion runs work through the whole shortlist; exclusion runs only re-check
    # the pages their source inclusion run kept.
    _shortlist_total = [None]   # memoised (one count per request, only if needed)

    def shortlist_total():
        if _shortlist_total[0] is None and category:
            try:
                _shortlist_total[0] = cached_count(conn, **_effective_filters(conn, category))
            except Exception:
                _shortlist_total[0] = None
        return _shortlist_total[0]

    by_id = {r["run_id"]: r for r in runs}
    for r in runs:
        if "exclusion" in (r.get("phase") or "").lower():
            src = by_id.get(r.get("source_run_id"))
            r["target"] = src.get("kept") if src else None   # exclusion re-checks the inclusion's keeps
        else:
            r["target"] = shortlist_total()
    out = {"runs": runs, "active": _active_run(conn, cid)}
    conn.close()
    return JSONResponse(out)


@app.get("/api/categories/{cid}/active-run")
def api_active_run_summary(request: Request, cid: int):
    """Summary of the active run for the Active Run tab: its phase chain
    (inclusion → exclusion) plus a status of complete / incomplete / in_progress."""
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    try:
        run_id = _active_run(conn, cid)
        run = evaluate.get_run(conn, run_id) if run_id else None
        if not run:
            return JSONResponse({"run": None})
        chain = evaluate.run_chain(conn, run_id)
        category = cat.get_category(conn, cid)
        try:
            shortlist_total = cached_count(conn, **_effective_filters(conn, category)) if category else None
        except Exception:
            shortlist_total = None
        continue_reason = evaluate.continuable_reason(chain, shortlist_total)
        chain_ids = {r["run_id"] for r in chain}
        ent = _bg_evals.get(cid)
        in_progress = bool(ent and ent["status"].get("running")
                           and ent["status"].get("run_id") in chain_ids)
        status = "in_progress" if in_progress else ("complete" if not continue_reason else "incomplete")
        rows = [{"phase": r.get("phase"), "provider": r.get("provider"),
                 "model": r.get("actual_model") or r.get("model"),
                 "pages": r.get("pages") or 0, "kept": r.get("kept") or 0,
                 "dropped": r.get("dropped") or 0, "in_tokens": r.get("in_tokens") or 0,
                 "out_tokens": r.get("out_tokens") or 0, "cost": r.get("cost") or 0}
                for r in chain]
        return JSONResponse({"run": {"run_id": run["run_id"], "name": run.get("name")},
                             "status": status, "chain": rows})
    finally:
        conn.close()


@app.get("/categories/{cid}/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(request: Request, cid: int, run_id: str):
    """Per-run detail: the LLM phase chain (inclusion → exclusion), model used,
    tokens and cost per phase, with rename."""
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    run = evaluate.get_run(conn, run_id)
    if not category or not run or str(run["category_id"]) != str(cid):
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    chain = evaluate.run_chain(conn, run_id)
    totals = {k: sum((r.get(k) or 0) for r in chain)
              for k in ("cost", "in_tokens", "out_tokens", "hit_tokens", "miss_tokens", "pages")}
    try:
        shortlist_total = cached_count(conn, **_effective_filters(conn, category))
    except Exception:
        shortlist_total = None
    commentary = evaluate.run_commentary(chain, shortlist_total, opened_run_id=run_id)
    continue_reason = evaluate.continuable_reason(chain, shortlist_total)
    unparsed = evaluate.unparsed_results(conn, [r["run_id"] for r in chain])
    phase_by_id = {r["run_id"]: r.get("phase") for r in chain}
    for u in unparsed:
        u["phase"] = phase_by_id.get(u["run_id"])
    resp = templates.TemplateResponse("run_detail.html", ctx(
        conn, request, category=category, run=run, chain=chain, totals=totals,
        commentary=commentary, unparsed=unparsed, continue_reason=continue_reason))
    conn.close()
    return resp


@app.post("/api/categories/{cid}/runs/{run_id}/activate")
async def api_activate_run(request: Request, cid: int, run_id: str):
    """Make this run the active run for the category — Evaluate / background runs
    then add to it."""
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    try:
        run = evaluate.get_run(conn, run_id)
        if not run or str(run["category_id"]) != str(cid):
            return JSONResponse({"error": "not found"}, status_code=404)
        settings.set_setting(conn, f"active_run_{cid}", run_id)
        return JSONResponse({"ok": True, "active": run_id})
    finally:
        conn.close()


@app.post("/api/categories/{cid}/runs/{run_id}/continue")
async def api_continue_run(request: Request, cid: int, run_id: str):
    """Make the phase that still needs work the active run so a background run
    finishes the LLM evaluation: resume an in-progress / short phase, or start the
    pending Phase-2 exclusion over the inclusion run's keeps. Returns the run that
    is now active, or {done: true} if there is nothing left to do."""
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    conn = connect()
    try:
        category = cat.get_category(conn, cid)
        run = evaluate.get_run(conn, run_id)
        if not category or not run or str(run["category_id"]) != str(cid):
            return JSONResponse({"error": "not found"}, status_code=404)
        chain = evaluate.run_chain(conn, run_id)
        try:
            shortlist_total = cached_count(conn, **_effective_filters(conn, category))
        except Exception:
            shortlist_total = None
        by_id = {r["run_id"]: r for r in chain}

        def target_of(r):
            if "exclusion" in (r.get("phase") or "").lower():
                src = by_id.get(r.get("source_run_id"))
                return src.get("kept") if src else None
            return shortlist_total

        # 1) an unfinished phase, or a finished phase that stopped below its input.
        for r in chain:
            if not r.get("finished_at"):
                settings.set_setting(conn, f"active_run_{cid}", r["run_id"])
                return JSONResponse({"active": r["run_id"], "action": "resume"})
        for r in chain:
            tgt = target_of(r)
            if tgt is not None and (r.get("pages") or 0) < tgt:
                settings.set_setting(conn, f"active_run_{cid}", r["run_id"])
                return JSONResponse({"active": r["run_id"], "action": "resume"})

        # 2) inclusion finished with keeps but no exclusion yet → start Phase 2.
        have_excl = any("exclusion" in (r.get("phase") or "").lower() for r in chain)
        incl = next((r for r in chain if "inclusion" in (r.get("phase") or "").lower()), None)
        if incl and incl.get("finished_at") and (incl.get("kept") or 0) > 0 and not have_excl:
            excfg = _ai_config_for_phase(conn, evaluate.PHASE_EXCLUSION)
            new_id = evaluate.create_run(conn, cid, excfg["model"], excfg["provider"],
                                         phase=evaluate.PHASE_EXCLUSION, source_run_id=incl["run_id"])
            settings.set_setting(conn, f"active_run_{cid}", new_id)
            return JSONResponse({"active": new_id, "action": "start_exclusion"})

        return JSONResponse({"done": True})
    finally:
        conn.close()


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


def _dl_stamp() -> str:
    """UTC timestamp for download filenames: yy-mm-dd-hr-min."""
    return time.strftime("%y-%m-%d-%H-%M", time.gmtime())


def _safe_filename(name: str, default: str) -> str:
    """A safe download base filename from user input: drop any extension they typed,
    keep letters/digits/space/dot/dash/underscore, spaces -> dashes, cap the length."""
    base = re.sub(r"\.(csv|xlsx|json)$", "", (name or "").strip(), flags=re.I)
    base = re.sub(r"[^A-Za-z0-9._ -]", "", base).strip().replace(" ", "-")
    return base[:120] or default


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
                    headers={"Content-Disposition": f'attachment; filename="gov-uk-funnel-pages-{_dl_stamp()}.csv"'})


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


# ---- audit shortlist (browse/extract the pages at any funnel stage) -----
# Stage key -> label. Deterministic stages progressively narrow the corpus; the two
# LLM stages restrict to the pages a run kept.
_AUDIT_STAGES = [
    ("dept", "Department"),
    ("doctype", "Document type"),
    ("keyword", "Keyword (Input Shortlist)"),
    ("include", "Included (LLM inclusion keeps)"),
    ("final", "Final (after exclusion / adjudication)"),
]
_AUDIT_STAGE_KEYS = [k for k, _ in _AUDIT_STAGES]
_AUDIT_DEFAULT_FIELDS = [k for _, fs in _DOWNLOAD_SECTIONS for (k, l, d, dis, w) in fs if d]


def _stage_query(conn, category, stage):
    """Filters for export_rows/count at an audit stage, or None if an LLM stage has
    no run yet. Deterministic stages drop the later filters; LLM stages restrict the
    content rows to the URLs their run kept."""
    eff = _effective_filters(conn, category)
    if stage == "dept":
        return dict(organisations=eff["organisations"], document_types=(), keywords=(), match=eff["match"])
    if stage == "doctype":
        return dict(organisations=eff["organisations"], document_types=eff["document_types"],
                    keywords=(), match=eff["match"])
    if stage == "keyword":
        return dict(eff)
    inc = evaluate.latest_inclusion_run(conn, category["id"])
    if not inc:
        return None
    run_id = (evaluate.latest_exclusion_run(conn, inc) if stage == "final" else None) or inc
    return dict(organisations=(), document_types=(), keywords=(), match="any",
                extra_where=f"c.url IN (SELECT url FROM evaluation_results "
                            f"WHERE run_id = {shortlist._P} AND keep = 1)",
                extra_params=[run_id])


def _merge_extra(filters, q):
    """Combine a stage filter's extra_where with an optional title search (q)."""
    f = dict(filters)
    clauses, params = [], []
    ew = f.pop("extra_where", None)
    if ew:
        clauses.append(ew)
        params.extend(f.pop("extra_params", []))
    else:
        f.pop("extra_params", None)
    if q:
        clauses.append(f"LOWER(c.title) LIKE LOWER({shortlist._P})")
        params.append("%" + q + "%")
    if clauses:
        f["extra_where"] = " AND ".join(clauses)
        f["extra_params"] = params
    return f


@app.get("/api/categories/{cid}/audit-shortlist")
def api_audit_shortlist(request: Request, cid: int, stage: str = "keyword",
                        limit: int = 50, offset: int = 0, q: str = "",
                        fields: List[str] = Query(default=[])):
    if not authed(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    if stage not in _AUDIT_STAGE_KEYS:
        stage = "keyword"
    sq = _stage_query(conn, category, stage)
    if sq is None:
        conn.close()
        return JSONResponse({"stage": stage, "no_data": True, "rows": [], "keys": [], "total": 0,
                             "message": "No AI evaluation run yet — run it on the Semantic Match tab first."})
    keys_wanted = [f for f in fields if f in shortlist.EXPORT_FIELDS] or list(_AUDIT_DEFAULT_FIELDS)
    filters = _merge_extra(sq, (q or "").strip())
    try:
        keys, rows = shortlist.export_rows(conn, keys_wanted, limit=limit, offset=offset, **filters)
    except Exception as e:
        conn.close()
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    try:
        total = shortlist.count(conn, **filters)
    except Exception:
        total = None
    conn.close()
    return JSONResponse({"stage": stage, "keys": keys, "rows": rows, "total": total,
                         "limit": limit, "offset": offset, "q": (q or "").strip()})


@app.get("/categories/{cid}/download", response_class=HTMLResponse)
def download_page(request: Request, cid: int, stage: str = "keyword",
                  fields: List[str] = Query(default=[])):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    if stage not in _AUDIT_STAGE_KEYS:
        stage = "keyword"
    sq = _stage_query(conn, category, stage)
    total = None
    if sq is not None:
        try:
            total = shortlist.count(conn, **_merge_extra(sq, ""))
        except Exception:
            total = None
    # Columns come from the Audit shortlist selection (passed as ?fields=…); fall back
    # to the default set if the page is opened directly with none.
    preselect = [f for f in fields if f in shortlist.EXPORT_FIELDS] or list(_AUDIT_DEFAULT_FIELDS)
    resp = templates.TemplateResponse("download.html", ctx(
        conn, request, category=category, total=total,
        stage=stage, stage_label=dict(_AUDIT_STAGES).get(stage, stage),
        preselect=preselect, default_filename=f"gov-uk-audit-shortlist-{_dl_stamp()}"))
    conn.close()
    return resp


@app.get("/categories/{cid}/export")
def export_category(request: Request, cid: int, format: str = "csv", stage: str = "keyword",
                    filename: str = "", fields: List[str] = Query(default=[])):
    """Build the chosen-format, chosen-field export for an audit stage. Sync route ->
    runs in a threadpool so a large export doesn't block the event loop."""
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        conn.close()
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    if stage not in _AUDIT_STAGE_KEYS:
        stage = "keyword"
    sq = _stage_query(conn, category, stage)
    if sq is None:
        conn.close()
        return RedirectResponse(url=str(request.url_for("download_page", cid=cid)), status_code=303)
    keys, rows = shortlist.export_rows(conn, fields, **_merge_extra(sq, ""))
    conn.close()
    labels = [shortlist.EXPORT_FIELDS[k][1] for k in keys]
    name = _safe_filename(filename, f"gov-uk-audit-shortlist-{_dl_stamp()}")

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
    finding = guardrails.check(prompt)          # block personal data / prohibited language
    if finding:
        return JSONResponse({"reply": guardrails.refusal_message(finding)})
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


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, details_ok: int = 0, pw_ok: int = 0,
                 details_error: str = "", pw_error: str = ""):
    """The user's profile: their details + password (accounts mode) and the per-viewer
    UI display level. The display level is stored client-side (localStorage) and applied as
    a data-ui-level attribute; it needs no account. Higher levels reveal more detail."""
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    levels = [
        ("simple", "Simple",
         "Build categories, run the AI phase, and review and download the final results."),
        ("advanced", "Advanced",
         "Adds detailed data on the selection — almost log level."),
        ("expert", "Expert",
         "See everything except system configuration."),
        ("admin", "Admin",
         "See everything."),
    ]
    resp = templates.TemplateResponse("profile.html", ctx(
        conn, request, levels=levels, accounts_mode=(AUTH_MODE == "accounts"),
        me=current_user(request), details_ok=details_ok, pw_ok=pw_ok,
        details_error=details_error, pw_error=pw_error))
    conn.close()
    return resp


@app.post("/profile/details")
async def update_profile_details(request: Request):
    """Update the signed-in user's own first/last name (accounts mode). Email is the login
    identity and is not editable here."""
    if not authed(request):
        return login_redirect(request)
    profile_url = str(request.url_for("profile_page"))
    u = current_user(request)
    if not u:
        return RedirectResponse(url=profile_url, status_code=303)
    form = await request.form()
    first = (form.get("first_name") or "").strip()
    last = (form.get("last_name") or "").strip()
    conn = connect()
    try:
        if not first or not last:
            return RedirectResponse(url=profile_url + "?details_error=First+and+last+name+are+required.#details",
                                    status_code=303)
        accounts.update_user(conn, u["id"], first_name=first, last_name=last)
    finally:
        conn.close()
    return RedirectResponse(url=profile_url + "?details_ok=1#details", status_code=303)


@app.post("/profile/password")
async def change_own_password(request: Request):
    """Change the signed-in user's own password: requires the current password (accounts mode)."""
    if not authed(request):
        return login_redirect(request)
    profile_url = str(request.url_for("profile_page"))
    u = current_user(request)
    if not u:
        return RedirectResponse(url=profile_url, status_code=303)
    form = await request.form()
    old = form.get("current_password") or ""
    new = form.get("new_password") or ""
    confirm = form.get("confirm_password") or ""
    conn = connect()
    try:
        if new != confirm:
            msg = "The new passwords do not match."
            return RedirectResponse(url=profile_url + f"?pw_error={quote(msg)}#password", status_code=303)
        try:
            accounts.change_password(conn, u["id"], old, new)
        except ValueError as exc:
            return RedirectResponse(url=profile_url + f"?pw_error={quote(str(exc))}#password", status_code=303)
    finally:
        conn.close()
    return RedirectResponse(url=profile_url + "?pw_ok=1#password", status_code=303)


def _require_admin(request: Request):
    """The current user if they are an admin (accounts mode only), else None."""
    if AUTH_MODE != "accounts":
        return None
    u = current_user(request)
    return u if (u and roles.allows(u.get("role"), "admin")) else None


@app.post("/admin/users")
async def admin_create_user(request: Request):
    """Admin creates an account (accounts mode). Same fields as registration, plus role."""
    if not authed(request):
        return login_redirect(request)
    settings_url = str(request.url_for("settings_page"))
    if not _require_admin(request):
        return RedirectResponse(url=settings_url, status_code=303)
    form = await request.form()
    role = form.get("role") if form.get("role") in accounts.ROLES else "User"
    conn = connect()
    try:
        accounts.create_user(conn, email=(form.get("email") or ""),
                             first_name=(form.get("first_name") or ""),
                             last_name=(form.get("last_name") or ""),
                             password=(form.get("password") or ""), role=role)
    except accounts.EmailTakenError:
        return RedirectResponse(url=settings_url + "?user_error=exists#users", status_code=303)
    except ValueError as e:
        return RedirectResponse(url=settings_url + "?user_error=" + quote(str(e)) + "#users",
                                status_code=303)
    finally:
        conn.close()
    return RedirectResponse(url=settings_url + "?user_ok=1#users", status_code=303)


@app.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
def admin_edit_user_page(request: Request, user_id: str):
    if not authed(request):
        return login_redirect(request)
    if not _require_admin(request):
        return RedirectResponse(url=str(request.url_for("settings_page")), status_code=303)
    conn = connect()
    try:
        u = accounts.get_user(conn, user_id)
        if not u:
            return RedirectResponse(url=str(request.url_for("settings_page")) + "#users", status_code=303)
        return templates.TemplateResponse("user_edit.html", ctx(
            conn, request, u=u, account_roles=accounts.ROLES, account_statuses=accounts.STATUSES))
    finally:
        conn.close()


@app.post("/admin/users/{user_id}/edit")
async def admin_update_user(request: Request, user_id: str):
    if not authed(request):
        return login_redirect(request)
    if not _require_admin(request):
        return RedirectResponse(url=str(request.url_for("settings_page")), status_code=303)
    form = await request.form()
    role = form.get("role") if form.get("role") in accounts.ROLES else None
    status = form.get("account_status") if form.get("account_status") in accounts.STATUSES else None
    conn = connect()
    try:
        accounts.update_user(conn, user_id, first_name=form.get("first_name"),
                             last_name=form.get("last_name"), role=role, account_status=status)
    finally:
        conn.close()
    return RedirectResponse(url=str(request.url_for("settings_page")) + "?user_ok=1#users", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, user_ok: int = 0, user_error: str = ""):
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
         "current": settings.get_setting(conn, PHASE_MODEL_KEYS[ph], ""),
         "mode_key": PHASE_MODE_KEYS[ph], "mode": _phase_mode(conn, ph),
         "provider": _ai_config_for_phase(conn, ph).get("provider")}
        for ph in (evaluate.PHASE_INCLUSION, evaluate.PHASE_EXCLUSION, evaluate.PHASE_ADJUDICATION)]
    accounts_mode = AUTH_MODE == "accounts"
    users = accounts.list_users(conn) if accounts_mode else None
    resp = templates.TemplateResponse("settings.html", ctx(
        conn, request, active_nav="settings", models=models,
        accounts_mode=accounts_mode, users=users, account_roles=accounts.ROLES,
        user_ok=user_ok, user_error=user_error,
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
    if "active_role" in form:
        roles.set_role(conn, form.get("active_role"))
    active = form.get("active_model_id")
    if active:
        settings.set_setting(conn, "active_model_id", active)
    # Per-phase model selection (Inclusion / Exclusion / Adjudication).
    for ph, key in PHASE_MODEL_KEYS.items():
        if key in form:
            settings.set_setting(conn, key, (form.get(key) or "").strip())
    # Per-phase execution mode (synchronous | batch).
    for ph, key in PHASE_MODE_KEYS.items():
        if key in form:
            settings.set_setting(conn, key, evaluate.normalise_mode(form.get(key)))
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
    # Guarded so saving another Settings tab (which doesn't post these) can't reset them.
    if "ai_daily_budget" in form:
        try:
            settings.set_setting(conn, "ai_daily_budget", str(float(form.get("ai_daily_budget") or DEFAULT_DAILY_BUDGET)))
        except ValueError:
            pass
    if "ai_max_docs_per_run" in form:
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
