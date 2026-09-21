# Upgrade / Merge Runbook — bring the public instance up to date

**Goal:** the live **public** instance (created ~Fri 2026‑09‑19 13:50, on Friday's code)
carries the real **users and shortlists**; the **`.148`** instance (18.171.159.148) carries the
complete, backfilled **corpus** plus the latest code and migrated schema. This runbook **merges
the public box's users + shortlists onto `.148`**, rebuilds derived data, then **cuts the public
endpoint over to `.148`** (Direction 1 — the recommended, low‑data‑movement path).

> Direction 2 (keep the public box, ship it the corpus) is summarised in the Appendix.

---

## 0. Facts, roles, and fill‑ins

| Role | Host | Notes |
|---|---|---|
| **SOT** (source of truth / target) | `18.171.159.148` | Complete corpus, latest code, schema migrated. DB `gov_uk_corpus` (Postgres), systemd unit `corpus-shortlist` on :8600. SSH: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148` |
| **PUB** (public box, to be merged in) | `<FILL: public host/IP>` | Live users + shortlists, Friday's code. DB `<FILL: db name, expect gov_uk_corpus>`. SSH: `<FILL>` |

**Before you start, replace every `<FILL: …>`.** Commands assume Postgres on both, the `postgres`
superuser reachable via peer auth (`sudo -u postgres psql gov_uk_corpus`), and the same DB name/role
on both. Verify that assumption in Step 1.

**Merge payload** (copied PUB → SOT):
`auth.users`, `auth.user_sessions`, `auth.audit_log`, `categories`, `evaluation_runs`,
`evaluation_results`, `category_audit`, `page_feedback`, and the category‑scoped `app_settings`
rows (`key LIKE 'active_run_%'`). Optional: `ai_usage` (spend history).

**Never copied:** corpus tables (`content`, `page_organisations`, `page_links`, `redirects`,
`organisations`, `organisation_hierarchy`, `sitemap`, `fetch_log`, `runs`) — already complete on SOT.
Derived tables (`category_shortlist_pages`, `category_page_counts`, `category_search_pages`,
`organisation_page_counts`) — **rebuilt**, not copied.

**Prerequisites / cautions**
- **Maintenance window.** Public users are logged out at cutover.
- **TLS / domain:** confirm `.148` can serve the public domain with a valid cert (nginx/reverse
  proxy or CDN in front of :8600). This is the main non‑DB prerequisite for cutover — sort it before Step 8.
- Keep global `app_settings` on SOT (daily budget, per‑phase model) — bring only PUB's `active_run_%` rows.

---

## 1. Pre‑flight (no changes yet)

```bash
# Versions
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148 'cd ~/gov-uk-corpus && git rev-parse --short HEAD'
ssh <FILL:PUB> 'cd ~/gov-uk-corpus && git rev-parse --short HEAD'
```

Record baseline counts on **both** (compare after):

```sql
-- run on SOT and on PUB
SELECT
  (SELECT count(*) FROM categories)          AS categories,
  (SELECT count(*) FROM auth.users)          AS users,
  (SELECT count(*) FROM evaluation_runs)     AS runs,
  (SELECT count(*) FROM evaluation_results)  AS results;
```

Confirm PUB's DB name and that `auth` schema exists (`\dn`, `\dt auth.*`).

---

## 2. Back up BOTH boxes — mandatory rollback point

```bash
STAMP=$(date +%Y%m%d-%H%M)
# SOT
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148 \
  "sudo -u postgres pg_dump -Fc gov_uk_corpus > /tmp/sot-$STAMP.dump && ls -la /tmp/sot-$STAMP.dump"
# PUB
ssh <FILL:PUB> "sudo -u postgres pg_dump -Fc <FILL:db> > /tmp/pub-$STAMP.dump && ls -la /tmp/pub-$STAMP.dump"
```

Copy both dumps **off the boxes** (scp to your machine). Optionally take Lightsail snapshots of both
instances now. **Do not proceed until both dumps exist off‑box.**

---

## 3. Confirm SOT is on the target code + schema

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148 'cd ~/gov-uk-corpus && \
  git pull origin main && sudo systemctl restart corpus-shortlist && sleep 3 && systemctl is-active corpus-shortlist'
```

Verify the migrated columns exist (idempotent — the restart's `init_db` applies them):

```sql
SELECT column_name FROM information_schema.columns
WHERE table_name='evaluation_runs' AND column_name IN ('run_status','pid','host','heartbeat_at');
SELECT 1 FROM information_schema.columns WHERE table_name='evaluation_results' AND column_name='raw_reply';
```

---

## 4. Remove SOT's test shortlists (so only the real, merged ones remain)

SOT's categories are dev/test artifacts; the real shortlists live on PUB. **List them and eyeball
first**, then delete the categories and everything scoped to them.

```sql
-- REVIEW: these are the categories currently on SOT (all expected to be test/dev)
SELECT id, COALESCE(description,slug) AS name, owner_email, created_at FROM categories ORDER BY created_at;
```

```sql
-- DELETE test/dev category data on SOT (run inside a transaction; keeps corpus + users intact)
BEGIN;
  DELETE FROM evaluation_results WHERE run_id IN (SELECT run_id FROM evaluation_runs);      -- all runs are test on SOT
  DELETE FROM evaluation_runs;
  DELETE FROM category_shortlist_pages;
  DELETE FROM category_search_pages;
  DELETE FROM category_page_counts;
  DELETE FROM category_audit;
  DELETE FROM page_feedback;
  DELETE FROM app_settings WHERE key LIKE 'active_run_%';
  DELETE FROM categories;
COMMIT;
```

> Leave SOT's own admin user in `auth.users` for now (you need it to log in and verify). If PUB
> contains that same email, resolve the collision in Step 6; otherwise remove the SOT admin after cutover.

---

## 5. Export the merge payload from PUB

```bash
STAMP=$(date +%Y%m%d-%H%M)
ssh <FILL:PUB> "sudo -u postgres pg_dump <FILL:db> --data-only --no-owner \
  -t auth.users -t auth.user_sessions -t auth.audit_log \
  -t categories -t evaluation_runs -t evaluation_results \
  -t category_audit -t page_feedback \
  > /tmp/merge-$STAMP.sql
  # category-scoped settings (active-run pointers) only:
  sudo -u postgres psql <FILL:db> -Atc \
    \"COPY (SELECT key,value FROM app_settings WHERE key LIKE 'active_run_%') TO STDOUT\" \
    > /tmp/merge-settings-$STAMP.tsv
  ls -la /tmp/merge-$STAMP.sql /tmp/merge-settings-$STAMP.tsv"
# bring both to SOT
scp <FILL:PUB>:/tmp/merge-$STAMP.sql /tmp/ && scp /tmp/merge-$STAMP.sql ubuntu@18.171.159.148:/tmp/    # via your machine, or rsync direct
scp <FILL:PUB>:/tmp/merge-settings-$STAMP.tsv /tmp/ && scp /tmp/merge-settings-$STAMP.tsv ubuntu@18.171.159.148:/tmp/
```

> `--data-only` emits `COPY` blocks (fast, order‑preserving). Optional extras: add `-t ai_usage` to
> keep PUB's spend ledger. `auth.user_sessions` is optional — skip it to force a clean re‑login.

---

## 6. Collision check, then import into SOT

**Collision check first** (after Step 4, SOT has no categories/runs, so these should return 0):

```sql
-- On SOT: verify nothing about to be imported already exists
SELECT count(*) FROM categories;          -- expect 0
SELECT count(*) FROM evaluation_runs;      -- expect 0
-- Users may overlap by email if SOT's admin == a PUB user; check:
SELECT email FROM auth.users;              -- note SOT's admin email(s)
```

If a PUB user shares SOT's admin email, decide which row wins (keep PUB's, delete SOT's admin row
before import, or edit the dump). Resolve before importing.

**Import** (as `postgres`; disable FK triggers so `auth` order doesn't bite):

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148 \
  "sudo -u postgres psql gov_uk_corpus -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
SET session_replication_role = replica;   -- defer FK checks (auth.*)
\i /tmp/merge-$STAMP.sql
SET session_replication_role = default;
COMMIT;
SQL"
# restore the active-run settings
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148 \
  "sudo -u postgres psql gov_uk_corpus -c \"\\copy app_settings(key,value) FROM '/tmp/merge-settings-$STAMP.tsv'\""
```

> Replace `$STAMP` with the actual value from Step 5. If the import errors, the `BEGIN…COMMIT`
> rolls back cleanly — fix and retry.

---

## 7. Rebuild derived data on SOT

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148 \
  'cd ~/gov-uk-corpus && set -a && . ~/gov-uk-corpus.env && set +a && \
   PYTHONPATH=/home/ubuntu/gov-uk-corpus .venv/bin/python -m govuk_corpus.category_counts'
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148 'sudo systemctl restart corpus-shortlist'
```

This rebuilds `category_shortlist_pages` (membership under **canonical URLs** + `matched_keywords`)
and `category_page_counts` for the imported shortlists. `category_search_pages` (GOV.UK hybrid
coverage) regenerates per shortlist on demand; `organisation_page_counts` is already complete.

---

## 8. Verify on SOT — **before** cutover

```sql
-- counts should now match PUB's Step‑1 baseline
SELECT (SELECT count(*) FROM categories) AS categories,
       (SELECT count(*) FROM auth.users) AS users,
       (SELECT count(*) FROM evaluation_runs) AS runs,
       (SELECT count(*) FROM evaluation_results) AS results;
-- membership rebuilt for imported shortlists (should be > 0 where a shortlist has matches)
SELECT category_id, count(*) FROM category_shortlist_pages GROUP BY category_id ORDER BY 2 DESC LIMIT 5;
```

```bash
# app up, login reachable
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-2.pem ubuntu@18.171.159.148 \
  'curl -s -o /dev/null -w "login %{http_code}\n" http://localhost:8600/login'
```

Manually: log in as a **real user** from PUB, open a known shortlist → the Shortlist/Pages loads,
`matched_keywords` populate, the funnel and AI‑pipeline tabs render, a run's detail opens.

**Do not cut over until this passes.**

---

## 9. Cut the public endpoint over to SOT

Pick one (in order of preference):

- **Move the static IP** (cleanest on Lightsail): detach PUB's static IP, attach it to SOT. DNS/TLS
  unchanged. Brief blip only.
- **Repoint DNS**: change the A record to `18.171.159.148`; wait for TTL. Lower the TTL a day ahead.
- **Reverse proxy**: point the existing front door (nginx/CDN) at SOT.

Ensure the front door terminates TLS for the public domain and forwards to SOT:8600. Then smoke‑test
the **public URL**: login, one shortlist, one run detail, the Sustainability page.

---

## 10. Post‑cutover

- Keep **PUB untouched** for at least a few days as instant rollback (its DB is exactly Friday+users).
- Watch `journalctl -u corpus-shortlist -f` and error rates for the first hour.
- Once satisfied, remove SOT's leftover test admin user if it isn't a real account.

---

## Rollback

| Failure | Action |
|---|---|
| Cutover misbehaves (DNS/IP/TLS) | Revert the IP/DNS/proxy to **PUB** — it still holds Friday's fully‑working state. No data loss. |
| Merge corrupted SOT | Restore SOT from `/tmp/sot-<STAMP>.dump`: `pg_restore --clean --if-exists -d gov_uk_corpus /tmp/sot-<STAMP>.dump` (as `postgres`), restart. |
| Need to re‑try the merge | Restore SOT from its Step‑2 dump and start again from Step 4. |

---

## Appendix — Direction 2 (keep the public box; ship it the corpus)

Use only if the public endpoint **must** stay on PUB and cannot move to SOT.

1. On PUB: `git pull origin main && sudo systemctl restart corpus-shortlist` (schema auto‑migrates).
2. `pg_dump -Fc` the **corpus** tables from SOT (`content`, `page_organisations`, `page_links`,
   `redirects`, `organisations`, `organisation_hierarchy`, `sitemap`, `fetch_log`, `runs`,
   `organisation_page_counts`) and restore into PUB (`--clean --if-exists` those tables only).
   This is ~877k `content` rows + relations — large; expect real transfer + downtime.
3. Rebuild derived data on PUB: `python -m govuk_corpus.category_counts`, and
   `python -m govuk_corpus.build_search_text --rescan` if PUB's `search_text` predates org‑name stripping.
4. Verify as in Step 8; no cutover needed (URL unchanged).

Trade‑off: no endpoint change, but you move the whole corpus and take the risk on the live box.
Direction 1 moves kilobytes onto an already‑verified instance and is preferred.
