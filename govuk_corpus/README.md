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
| `stage_sitemap.py` | **Stage 0** — sitemap frontier refresh (offline dir or live), `lastmod` tracking. |
| `stage_align.py` | **Stage 1** — fetch, hash, upsert with provenance; reads the frontier. |
| `stage_redirects.py` | **Stage 2** — resolve redirect chains to final (depth-capped, cycle-safe), import destination. |
| `pilot.py` | Runnable pilot: seed / existing-db / `--from-frontier`. |

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

## Run Stage 0 (sitemap frontier)
```bash
# offline: parse the sub-sitemaps already in ./sitemaps/
python3 -m govuk_corpus.stage_sitemap --db data/pilot.db --from-dir sitemaps
# live: fetch a couple of sub-sitemaps from gov.uk
python3 -m govuk_corpus.stage_sitemap --db data/pilot.db --live --limit-sitemaps 2
# then align pages from the frontier
python3 -m govuk_corpus.pilot --db data/pilot.db --from-frontier 50
# resolve redirects to their final destination and import it
python3 -m govuk_corpus.stage_redirects --db data/pilot.db
```

## Status / next
- **Done:** canonicalisation, schema + provenance (`runs`/`fetch_log`), hashing,
  **Stage 0** (sitemap frontier + `lastmod`), **Stage 1** align + frontier wiring,
  **Stage 2** (redirect chains → final → import). **23 unit tests pass.** Verified
  end-to-end incl. a real redirect resolving to its collection page.
- **Next:** Stage 3 (attachment/child-page expansion into the one corpus via
  `page_links`), then the Lightsail Postgres migration.
