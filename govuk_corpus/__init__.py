"""GOV.UK corpus rebuild — clean, instrumented pilot package.

Modules:
  canonical    URL canonicalisation gate (used on every insert and join)
  hashing      content hashing for change detection
  config       pilot config + DEFRA scope + seed frontier
  db           SQLite access (Postgres-ready), runs + fetch_log provenance
  extract      deterministic field + organisation extraction from /api/content
  stage_align  Stage 1: fetch, hash, upsert with provenance
  pilot        runnable first-session pilot (DEFRA slice)
"""
