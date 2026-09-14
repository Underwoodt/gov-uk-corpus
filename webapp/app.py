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

import hashlib
import hmac
import io
import json
import os
from typing import Dict, List

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from govuk_corpus import categories as cat
from govuk_corpus import facets, shortlist
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


# ---- helpers -------------------------------------------------------------
def connect():
    return db.connect(DB_PATH, statement_timeout_ms=15000) if _is_pg() else db.connect(DB_PATH)


def _is_pg() -> bool:
    return db.__name__.endswith("db_pg")


def corpus_meta(conn) -> str:
    if "s" not in _META_CACHE:
        try:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM content WHERE is_redirect = 0 AND content_hash IS NOT NULL"
            ).fetchone()["n"]
            _META_CACHE["s"] = f"Newest snapshot · {n:,} pages"
        except Exception:
            _META_CACHE["s"] = "Newest snapshot"
    return _META_CACHE["s"]


def ctx(conn, request: Request, **extra) -> dict:
    base = {"request": request, "corpus_meta": corpus_meta(conn)}
    base.update(extra)
    return base


def authed(request: Request) -> bool:
    if not PASSWORD:
        return True
    token = request.cookies.get(COOKIE, "")
    want = hmac.new(PASSWORD.encode(), b"ok", hashlib.sha256).hexdigest()
    return hmac.compare_digest(token, want)


def login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url=str(request.url_for("login")), status_code=303)


def form_values(form) -> dict:
    """Flatten a submitted form into a category data dict (checkbox lists -> newline text)."""
    d = {k: (form.get(k) or "").strip() for k in
         ("slug", "owner_email", "keywords", "inclusion_context", "exclusion_context",
          "adjudication_hints_keep", "adjudication_hints_drop")}
    d["dept_slugs"] = "\n".join(form.getlist("dept_slugs"))
    d["document_type_slugs"] = "\n".join(form.getlist("document_type_slugs"))
    d["description"] = cat.prettify(d.get("slug"))  # keep the Streamlit list name sensible
    return d


# ---- auth ---------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login(request: Request, bad: int = 0):
    body = ("<div class='error-summary'><h2>There is a problem</h2>"
            "<ul><li>Incorrect password.</li></ul></div>" if bad else "")
    return HTMLResponse(
        f"""<!doctype html><meta charset=utf-8><link rel=stylesheet href='/static/govuk.css'>
        <div class='masthead'><div class='wrap'><span class='brand'>Defra</span></div></div>
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
            r["pages_kept"] = "{:,}".format(shortlist.count(
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
    return RedirectResponse(url=str(request.url_for("preview_category_page", cid=cid)), status_code=303)


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
    return RedirectResponse(url=str(request.url_for("preview_category_page", cid=cid)), status_code=303)


def _form_ctx(conn, request, category, values, errors) -> dict:
    return ctx(
        conn, request,
        is_edit=category is not None,
        category=category,
        action=(str(request.url_for("update_category", cid=category["id"])) if category
                else str(request.url_for("create_category"))),
        v=values or {},
        orgs=facets.organisations(conn),
        doc_types=facets.document_types(conn),
        selected_depts=set(cat.parse_list((values or {}).get("dept_slugs"))),
        selected_types=set(cat.parse_list((values or {}).get("document_type_slugs"))),
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


@app.get("/categories/{cid}", response_class=HTMLResponse)
def preview_category_page(request: Request, cid: int):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    category["display_name"] = cat.prettify(category.get("slug")) or (category.get("description") or "Untitled")
    filters = _filters(category)
    funnel = shortlist.selection_funnel(
        conn, organisations=filters["organisations"],
        document_types=filters["document_types"], keywords=filters["keywords"])
    total = shortlist.count(conn, **filters)
    rows = shortlist.shortlist_rows(conn, limit=500, **filters) if total else []
    sql, params = shortlist.build_query(include_title=True, limit=10000, **filters)
    pretty = shortlist.pretty_sql(shortlist.interpolate_sql(sql, params))
    conn.close()
    return templates.TemplateResponse("preview.html", ctx(
        connect(), request, category=category, funnel=funnel, total=total,
        rows=rows, truncated=total > len(rows), sql=pretty))


@app.get("/categories/{cid}/download")
def download_category(request: Request, cid: int, fmt: str = "csv"):
    if not authed(request):
        return login_redirect(request)
    conn = connect()
    category = cat.get_category(conn, cid)
    if not category:
        return RedirectResponse(url=str(request.url_for("list_categories_page")), status_code=303)
    rows = shortlist.shortlist_rows(conn, limit=100000, **_filters(category))
    conn.close()
    name = category.get("slug") or f"category-{cid}"
    if fmt == "txt":
        return PlainTextResponse("\n".join(r["url"] for r in rows),
                                 headers={"Content-Disposition": f'attachment; filename="{name}.txt"'})
    buf = io.StringIO()
    import csv
    w = csv.writer(buf); w.writerow(["url", "title"])
    for r in rows:
        w.writerow([r["url"], r["title"] or ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@app.on_event("startup")
def _startup():
    conn = connect()
    db.init_db(conn)
    conn.close()
