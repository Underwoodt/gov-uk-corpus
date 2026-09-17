# Runbook — GOV.UK Content Shortlist Builder

Step-by-step setup for the FastAPI + Jinja **Content Shortlist Builder**
(`webapp/app.py` + the `govuk_corpus/` package).

Two paths:

- **[A. Local development](#a-local-development-sqlite)** — SQLite, no Postgres, no LLM keys needed.
- **[B. Production](#b-production-postgres--systemd)** — Postgres on Lightsail/EC2, systemd, port 8600.

See [README.md](README.md) for what the app does and [database.md](database.md) for the schema.

---

## A. Local development (SQLite)

The data layer defaults to SQLite whenever `DB_BACKEND` / `DB_HOST` are unset
(see [backend.py](govuk_corpus/backend.py)), so local dev needs nothing but Python.

### A1. Clone and create a virtualenv

```bash
git clone https://github.com/AI-Accelerator-Defra/gov-uk-corpus.git
cd gov-uk-corpus

python3 -m venv .venv            # Python 3.9+ (3.11+ recommended)
source .venv/bin/activate
pip install -r requirements.txt
```

### A2. Build a local SQLite corpus

The app reads a `content` corpus. Locally you seed a small one with the pilot loader
([pilot.py](govuk_corpus/pilot.py)). Pick whichever seed source you have:

```bash
# Option 1 — from an existing content.db (read-only slice; recommended if you have it)
python -m govuk_corpus.pilot --db data/pilot.db \
    --from-content-db ~/Downloads/content.db --limit 200

# Option 2 — from the built-in DEFRA seed frontier (no external file needed)
python -m govuk_corpus.pilot --db data/pilot.db
```

This creates `data/pilot.db` and applies the schema (`db.init_db`). Re-running is safe —
unchanged pages report `unchanged` (content-hash change detection).

### A3. Run the app

```bash
CORPUS_DB=data/pilot.db DASHBOARD_PASSWORD=devpass \
    uvicorn webapp.app:app --reload --port 8600
```

Open <http://localhost:8600>, sign in with `devpass` (the `DASHBOARD_PASSWORD` you set).

- `--reload` restarts on code changes.
- LLM evaluation needs a provider key (`ANTHROPIC_API_KEY`); without one, the
  deterministic funnel, keyword matching, and export all still work — only the
  Semantic/LLM passes are unavailable.

### A4. Run the tests

```bash
python -m unittest discover -s tests
# ~194 tests on SQLite. Postgres-only (auth) tests skip unless DB_BACKEND=postgres + DB_* are set.
```

### A5. Try it end to end

1. **Categories → New** (or *Start from example → slurry*).
2. Add organisations, document types, keywords, and inclusion/exclusion context; save.
3. Open the category → **Selection funnel** narrows the corpus (org → doc type → keyword).
4. **Evaluate** to run the LLM inclusion/exclusion passes (needs a key), or skip.
5. **Audit Shortlist** tab → pick a stage (Department / Document type / Keyword /
   Included / Final), choose columns, **Download…** → CSV / XLSX / JSON.

---

## B. Production (Postgres + systemd)

Production runs `uvicorn webapp.app:app` on port **8600** under systemd, against
Postgres, with secrets in an env file. Target host: Lightsail/EC2 (Ubuntu),
e.g. `18.171.159.148:8600`.

> **Safety:** the production database holds the real corpus (~877k pages). Never run
> DDL against it casually, never paste the DB password into a shell as an argument
> (use the env file), and prefer `sudo -u postgres psql` (peer auth) for admin.

### B1. System packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

### B2. Database and least-privilege role

```bash
sudo -u postgres psql
```
```sql
CREATE DATABASE gov_uk_corpus;
CREATE USER corpus WITH PASSWORD 'CHANGE_ME_STRONG';
GRANT CONNECT ON DATABASE gov_uk_corpus TO corpus;
\c gov_uk_corpus
GRANT USAGE, CREATE ON SCHEMA public TO corpus;   -- app creates its own tables on init
\q
```

The app role does **not** need superuser or CREATEDB. It needs CREATE on the database
it uses so `init_db` can create the app tables. (The auth schema, when enabled, lives
in a separate `auth` schema — see [schema_auth.sql](govuk_corpus/schema_auth.sql).)

### B3. Fetch the code and install

```bash
cd /home/ubuntu
git clone https://github.com/AI-Accelerator-Defra/gov-uk-corpus.git
cd gov-uk-corpus
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### B4. The environment file (secrets)

Create `/home/ubuntu/gov-uk-corpus.env` (this is what the systemd unit loads):

```ini
DB_BACKEND=postgres
DB_HOST=localhost
DB_PORT=5432
DB_NAME=gov_uk_corpus
DB_USER=corpus
DB_PASSWORD=CHANGE_ME_STRONG

DASHBOARD_PASSWORD=CHANGE_ME_LOGIN

# LLM providers (only on the server; needed for the Semantic/LLM evaluation passes)
ANTHROPIC_API_KEY=sk-ant-...
```

Lock it down:

```bash
chmod 600 /home/ubuntu/gov-uk-corpus.env
```

### B5. Initialise the schema (once)

Apply the app schema to a **fresh** database. Skip this if the DB already has the
app tables (as the existing pilot DB does).

```bash
cd /home/ubuntu/gov-uk-corpus
set -a; . /home/ubuntu/gov-uk-corpus.env; set +a
.venv/bin/python -c "from govuk_corpus.backend import db; c=db.connect(); db.init_db(c); print('schema applied')"
```

`init_db` runs [schema_pg.sql](govuk_corpus/schema_pg.sql) with `CREATE TABLE IF NOT
EXISTS`, so it is safe to re-run and will not drop data.

### B6. Load the systemd service

The unit is version-controlled at [deploy/corpus-shortlist.service](deploy/corpus-shortlist.service).
It runs uvicorn on `0.0.0.0:8600`, loads the env file, and restarts on failure.
Confirm the paths (`WorkingDirectory`, `ExecStart` venv, `EnvironmentFile`, `User`)
match this host, then install it:

```bash
sudo cp deploy/corpus-shortlist.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now corpus-shortlist
```

### B7. Verify

```bash
systemctl status corpus-shortlist --no-pager
journalctl -u corpus-shortlist -n 50 --no-pager
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8600/login   # expect 200
```

Then browse to `http://<host-ip>:8600` and sign in with `DASHBOARD_PASSWORD`.
(Open port 8600 in the Lightsail/EC2 firewall, or front it with nginx.)

---

## Deploying updates

```bash
cd /home/ubuntu/gov-uk-corpus
git pull
source .venv/bin/activate
pip install -r requirements.txt          # only if requirements changed
# If a change adds/alters tables, re-run the B5 schema init (IF NOT EXISTS is safe).
sudo systemctl restart corpus-shortlist
journalctl -u corpus-shortlist -n 30 --no-pager
```

---

## Go-live before HTTPS is ready (deploy now, harden later)

You can deploy today over the existing **HTTP** and tighten security once HTTPS/nginx
is in place. There are two independent halves — do **A** freely; treat **B** as a
*testing* deploy, not real go-live, until HTTPS lands.

### Part A — ship the accumulated app changes (safe over HTTP, do anytime)

This is the current shared-password app (`AUTH_MODE` unset → `shared`) on the same HTTP
you already run. HTTPS changes nothing here; there is **no** security regression.

```bash
cd /home/ubuntu/gov-uk-corpus
git pull
source .venv/bin/activate
pip install -r requirements.txt          # only if requirements changed

# Schema: adds category_page_counts, gds_checks, gds_stars — all IF NOT EXISTS (safe).
set -a; . /home/ubuntu/gov-uk-corpus.env; set +a
.venv/bin/python -c "from govuk_corpus.backend import db; c=db.connect(); db.init_db(c); print('schema applied')"

# One-off backfills for the new features:
.venv/bin/python -m govuk_corpus.category_counts          # Categories-list page counts
.venv/bin/python -m govuk_corpus.build_readability --rescan   # GDS stars (prints star histogram)

sudo systemctl restart corpus-shortlist
journalctl -u corpus-shortlist -n 30 --no-pager
```

Send me the star histogram printed by `--rescan` so we can calibrate the star bands.
After deploy, hard-refresh the browser (**Cmd/Ctrl+Shift+R**) so the new CSS loads.

### Part B — turn accounts on over HTTP, then harden when HTTPS lands

`AUTH_MODE=accounts` **works over plain HTTP** because the session/CSRF cookies are only
marked `Secure` when you opt in (`_secure_cookies()` in [webapp/app.py](webapp/app.py)
is true only if `ENABLE_HSTS=1` **or** `SECURE_COOKIES=1`).

> ⚠️ **Over HTTP, passwords at login and the session token travel in cleartext.** This
> is fine for *you* exercising register/login/RBAC with throwaway passwords, but it is
> **not** safe for real users. Do **not** set `SECURE_COOKIES=1` / `ENABLE_HSTS=1` yet —
> `Secure` cookies are silently dropped over HTTP and login will appear to fail.

**Enable accounts over HTTP (testing):**

```bash
# 1. Create the auth schema (isolated 'auth' schema; corpus tables untouched, idempotent).
set -a; . /home/ubuntu/gov-uk-corpus.env; set +a
.venv/bin/python -c "from govuk_corpus.backend import db; from govuk_corpus import accounts; c=db.connect(); accounts.init_auth_schema(c); print('auth schema ready')"

# 2. In /home/ubuntu/gov-uk-corpus.env add:  AUTH_MODE=accounts
#    (leave ENABLE_HSTS / SECURE_COOKIES UNSET while on HTTP)
sudo systemctl restart corpus-shortlist
```

Then register the first accounts (DEFRA / Equal Experts email domains only) and test.

**Harden once HTTPS/nginx is live (no code change, env + restart):**

```bash
# In gov-uk-corpus.env add:  ENABLE_HSTS=1
#   (this alone flips cookies to Secure AND sends HSTS; SECURE_COOKIES=1 is the same flip
#    without HSTS if you want Secure cookies before committing to HSTS.)
sudo systemctl restart corpus-shortlist
```

Then invalidate any cleartext-era sessions so no HTTP-issued token stays valid — either
have everyone sign out and back in, or clear the session table once:

```bash
sudo -u postgres psql -d gov_uk_corpus -c "DELETE FROM auth.user_sessions;"
```

**Summary:** deploy Part A whenever; run Part B over HTTP only for your own testing with
burnable credentials; flip `ENABLE_HSTS=1` + force re-login the moment HTTPS is confirmed.

---

## Operations

| Task | Command |
|------|---------|
| Service status | `systemctl status corpus-shortlist` |
| Live logs | `journalctl -u corpus-shortlist -f` |
| Restart | `sudo systemctl restart corpus-shortlist` |
| Stop | `sudo systemctl stop corpus-shortlist` |
| DB shell (admin) | `sudo -u postgres psql -d gov_uk_corpus` |
| Corpus size | `SELECT count(*) FROM content;` |
| Recent eval runs | `SELECT * FROM evaluation_runs ORDER BY started_at DESC LIMIT 10;` |

### Sync vs batch evaluation
Each LLM phase can run **synchronously** or in **batch** — set per phase on the
**Settings** page. Background evaluation runs server-side (start/stop/status), so you
can leave the category page while a run completes; one background run per category,
multiple categories concurrently.

### AI spend
Set the **daily AI budget** and guard on Settings. Spend is tracked in the `ai_usage`
ledger; models are managed in the **AI model catalogue** (add / test / delete).

### Plain-English (GDS) audit re-scan
The Audit Results → Dashboard quality stars come from `content.gds_checks` / `gds_stars`,
populated by the readability backfill. After the checks or weights change, re-scan the
corpus (prints a star histogram to calibrate the star bands — see [docs/gds-audit.md](docs/gds-audit.md)):

```bash
set -a; . /home/ubuntu/gov-uk-corpus.env; set +a
.venv/bin/python -m govuk_corpus.build_readability --rescan
```

Without `--rescan` it only scans rows not yet scored. It's resumable (`--after <url>`).

### Nightly corpus cycle & category counts
The daily batch cycle is `python -m govuk_corpus.run_all` (Stage 0→3, then a
**tidy-up** phase). Its tidy-up recomputes each category's stored "pages kept" figure
into `category_page_counts`, so the **Categories** list reads a number instead of
running a live corpus count per row on every load. The figure is also refreshed
whenever a category is created or edited. To rebuild the counts on demand (e.g. after
a manual corpus load):

```bash
set -a; . /home/ubuntu/gov-uk-corpus.env; set +a
.venv/bin/python -m govuk_corpus.category_counts
```

---

## Troubleshooting

**Login page loads but nothing else / `500` on category pages**
The database has no app tables — run the B5 schema init. On SQLite, point `CORPUS_DB`
at a DB the pilot actually built (`data/pilot.db`).

**`no pg_hba.conf entry for host "<ip>"`**
The connecting host isn't allow-listed in Postgres. Add it to `pg_hba.conf` (or the
managed provider's allow-list) and reload. For a cloud test DB the allow-listed IP can
change with your network.

**`psycopg` import error locally**
You only need psycopg for Postgres. Local dev is SQLite — leave `DB_BACKEND`/`DB_HOST`
unset. To use Postgres locally, install deps (`pip install -r requirements.txt`) and
set the `DB_*` vars.

**Wrong backend selected**
Postgres is chosen when `DB_BACKEND=postgres` **or** `DB_HOST` is set. To force SQLite,
unset both and set `CORPUS_DB`.

**Semantic/LLM passes do nothing**
No provider key. Add `ANTHROPIC_API_KEY` (server: the env file). The deterministic
funnel, keyword matching, Audit Shortlist, and export work without any key.

**Permission denied creating tables (Postgres)**
The app role needs `CREATE` on the database/schema (B2). It must **not** be superuser.

---

## Reference

- App entry: [webapp/app.py](webapp/app.py) — routes, funnel, evaluation, export.
- Backend selector: [govuk_corpus/backend.py](govuk_corpus/backend.py).
- Schema: [schema.sql](govuk_corpus/schema.sql) (SQLite), [schema_pg.sql](govuk_corpus/schema_pg.sql) (Postgres), [schema_auth.sql](govuk_corpus/schema_auth.sql) (auth).
- Data model: [database.md](database.md).
- systemd unit: [deploy/corpus-shortlist.service](deploy/corpus-shortlist.service).
