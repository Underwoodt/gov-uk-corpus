# govuk_corpus — corpus rebuild (clean pilot package)

Clean, instrumented rebuild of the GOV.UK corpus. See the intent doc and build plan
in the vault: `projects/gov-uk-corpus/corpus-intent.md` and `build-plan-phase-a.md`.

This package is the **Phase A foundation**. It ports the proven logic from the older
`jobs/` + `scripts/` scripts into a run/provenance/hashing framework, targeting SQLite
now and Postgres on migration.

## Modules
| Module | Role |
|---|---|
| `canonical.py` | URL canonicalisation gate — used on every insert and join. Rejects malformed URLs, promotes `gov.uk` → `www.gov.uk`. |
| `hashing.py` | `content_hash` of raw `/api/content` JSON for change detection. |
| `config.py` | API base, rate limit, DEFRA scope, pilot seed frontier. |
| `schema.sql` | Pilot schema (SQLite, Postgres-ready): `runs`, `fetch_log`, `content`, `sitemap`, `page_organisations`, `page_links`, `redirects`. |
| `db.py` | SQLite access + run/provenance helpers. |
| `extract.py` | Deterministic field + organisation extraction from `/api/content`. |
| `stage_align.py` | **Stage 1** — fetch, hash, upsert with provenance. |
| `pilot.py` | Runnable first-session pilot (DEFRA slice). |

## Run the pilot
```bash
# built-in DEFRA seed frontier (a handful of live pages, rate-limited)
python3 -m govuk_corpus.pilot --db data/pilot.db

# run again → reports `unchanged` (content hash matched) = change detection working

# or seed from the existing corpus (read-only)
python3 -m govuk_corpus.pilot --from-content-db ~/Downloads/content.db --limit 15
```

## Tests
```bash
python3 -m unittest -v tests.test_canonical
```

## Status / next
- Done: canonicalisation (tested), schema + provenance (`runs`/`fetch_log`), hashing,
  Stage 1 align, pilot runner. Verified end-to-end on a live DEFRA slice
  (new → unchanged across two runs).
- Next: Stage 0 (sitemap refresh with `lastmod`), Stage 2 (consolidated redirects),
  Stage 3 (attachment/child-page expansion into the one corpus), then the Lightsail
  Postgres migration.
