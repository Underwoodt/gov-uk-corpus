-- GOV.UK corpus — pilot schema (SQLite).
-- Postgres-ready: keep types simple; on migration, TEXT timestamps -> timestamptz,
-- raw `content` TEXT -> JSONB, INTEGER flags -> boolean, add FK constraints.

-- One recorded execution of a stage (the unit of provenance).
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,           -- uuid4
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,              -- running | complete | failed
    stage       TEXT,                       -- sitemap | align | redirect | attachment
    scope       TEXT,                       -- e.g. "defra-pilot" | "whole-govuk"
    counters    TEXT,                       -- json blob
    notes       TEXT
);

-- Per-URL audit trail behind the run counters.
CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    stage       TEXT,
    url         TEXT,
    action      TEXT,   -- new | changed | unchanged | redirect | error | skipped | invalid | no_content_item
    http_status INTEGER,
    bytes       INTEGER,
    duration_ms INTEGER,
    error       TEXT,
    ts          TEXT
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_run ON fetch_log(run_id);

-- The one page corpus (sitemap pages, redirect targets and attachment/child pages).
CREATE TABLE IF NOT EXISTS content (
    url                TEXT PRIMARY KEY,     -- canonical https://www.gov.uk/...
    content            TEXT,                 -- raw /api/content JSON  (-> JSONB in PG)
    content_hash       TEXT,
    search_text        TEXT,                 -- HTML-stripped body for keyword search
    reading_age        REAL,                 -- estimated reading age (years) of search_text
    gds_english_score  REAL,                 -- GDS plain-English compliance score for search_text
    gds_findings       TEXT,                 -- readable summary of the GDS issues found
    source             TEXT,                 -- sitemap | attachment | redirect | seed | other
    document_type      TEXT,
    schema_name        TEXT,
    title              TEXT,
    description        TEXT,
    first_published_at TEXT,
    public_updated_at  TEXT,
    withdrawn          INTEGER DEFAULT 0,
    is_redirect        INTEGER DEFAULT 0,
    destination_url    TEXT,
    http_status        INTEGER,
    sitemap_lastmod    TEXT,                 -- NULL if not discovered via sitemap
    first_seen_run     TEXT,
    last_seen_run      TEXT,
    last_changed_run   TEXT,
    first_seen_at      TEXT,
    last_seen_at       TEXT,
    last_changed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_content_document_type ON content(document_type);
CREATE INDEX IF NOT EXISTS idx_content_source ON content(source);
-- Partial indexes over the "usable page" guard every shortlist count carries.
CREATE INDEX IF NOT EXISTS idx_content_usable
  ON content(url) WHERE is_redirect = 0 AND content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_content_usable_doctype
  ON content(document_type) WHERE is_redirect = 0 AND content_hash IS NOT NULL;

-- The sitemap frontier.
CREATE TABLE IF NOT EXISTS sitemap (
    url          TEXT PRIMARY KEY,
    sitemap_file TEXT,
    lastmod      TEXT,
    imported_at  TEXT
);

-- Organisations linked to a page (primary + related), normalised out of the JSON.
CREATE TABLE IF NOT EXISTS page_organisations (
    page_url                TEXT NOT NULL,
    organisation_content_id TEXT,
    organisation_slug       TEXT,
    role                    TEXT NOT NULL,   -- primary | related
    PRIMARY KEY (page_url, organisation_content_id, role)
);
CREATE INDEX IF NOT EXISTS idx_page_orgs_slug ON page_organisations(organisation_slug);

-- Parent -> child/attachment relationships between PAGES (both live in content).
CREATE TABLE IF NOT EXISTS page_links (
    parent_url TEXT NOT NULL,
    child_url  TEXT NOT NULL,
    relation   TEXT NOT NULL,               -- attachment | part | related
    PRIMARY KEY (parent_url, child_url, relation)
);

-- Redirect source -> final destination (Stage 2).
CREATE TABLE IF NOT EXISTS redirects (
    source_url      TEXT PRIMARY KEY,
    destination_url TEXT,
    http_status     INTEGER,
    resolved_run    TEXT,
    resolved_at     TEXT
);

-- Organisation registry + hierarchy (imported from the GOV.UK search-API org
-- aggregate). Lets selection expand a chosen organisation to its child departments
-- and resolve a page's organisation to its parent.
CREATE TABLE IF NOT EXISTS organisations (
    slug                 TEXT PRIMARY KEY,   -- matches page_organisations.organisation_slug
    title                TEXT,
    acronym              TEXT,
    content_id           TEXT,
    org_type             TEXT,
    org_state            TEXT,
    brand                TEXT,
    analytics_identifier TEXT
);

CREATE TABLE IF NOT EXISTS organisation_hierarchy (
    parent_slug TEXT NOT NULL,
    child_slug  TEXT NOT NULL,
    PRIMARY KEY (parent_slug, child_slug)
);
CREATE INDEX IF NOT EXISTS idx_org_hier_parent ON organisation_hierarchy(parent_slug);
CREATE INDEX IF NOT EXISTS idx_org_hier_child  ON organisation_hierarchy(child_slug);

-- AI inclusion-pass evaluation, tracked per run so different models can be compared.
CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id       TEXT PRIMARY KEY,
    category_id  INTEGER,
    phase        TEXT DEFAULT 'Phase 1 - Inclusion',   -- Phase 1 Inclusion | 2 Exclusion | 3 Adjudication
    provider     TEXT,             -- supplier: anthropic | deepseek | …
    model        TEXT,             -- requested/configured model id
    actual_model TEXT,             -- model the API actually served (from the response)
    started_at   TEXT,
    finished_at  TEXT,
    pages        INTEGER DEFAULT 0,
    kept         INTEGER DEFAULT 0,
    dropped      INTEGER DEFAULT 0,
    unparseable  INTEGER DEFAULT 0,
    cost         REAL DEFAULT 0,
    in_tokens    INTEGER DEFAULT 0,   -- total input tokens billed across the run
    out_tokens   INTEGER DEFAULT 0,   -- total output tokens billed across the run
    total_ms     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_cat ON evaluation_runs(category_id);

CREATE TABLE IF NOT EXISTS evaluation_results (
    run_id       TEXT NOT NULL,
    category_id  INTEGER,
    url          TEXT NOT NULL,
    keep         INTEGER,
    score        REAL,
    reason       TEXT,
    ms           INTEGER,
    created_at   TEXT,
    PRIMARY KEY (run_id, url)
);
CREATE INDEX IF NOT EXISTS idx_eval_results_run_keep ON evaluation_results(run_id, keep);

-- AI spend ledger (one row per successful model call) for the daily budget.
CREATE TABLE IF NOT EXISTS ai_usage (
    day           TEXT,
    created_at    TEXT,
    cost          REAL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    kind          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_day ON ai_usage(day);

-- User-editable AI models (supplier + model id + prices) for the assistant/evaluation.
CREATE TABLE IF NOT EXISTS ai_models (
    id            INTEGER PRIMARY KEY,  -- epoch-ms
    provider      TEXT,                 -- anthropic | deepseek
    model_id      TEXT,
    input_per_m   REAL,                 -- standard input rate = in_miss_off (USD / 1M)
    output_per_m  REAL,                 -- standard output rate = out_off (USD / 1M)
    in_hit_off    REAL,                 -- input, cache hit, off-peak
    in_hit_peak   REAL,                 -- input, cache hit, peak
    in_miss_off   REAL,                 -- input, cache miss, off-peak
    in_miss_peak  REAL,                 -- input, cache miss, peak
    out_off       REAL,                 -- output, off-peak
    out_peak      REAL,                 -- output, peak
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_models_provider ON ai_models(provider, model_id);

-- Small key/value app settings (e.g. which AI provider the UI uses).
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Per-page funnel audit for a category. Starting point is the organisation
-- filter (so we never log the whole corpus): every row passed the org filter,
-- and `outcome` says where it then dropped, or that it was included.
CREATE TABLE IF NOT EXISTS category_audit (
    category_id  INTEGER NOT NULL,
    url          TEXT NOT NULL,
    outcome      TEXT NOT NULL,   -- included | dropped: document type | dropped: keyword
    created_at   TEXT,
    PRIMARY KEY (category_id, url)
);
CREATE INDEX IF NOT EXISTS idx_category_audit_outcome ON category_audit(category_id, outcome);

-- Saved shortlist specs ("categories") — CRUD from the Shortlist Builder UI.
-- The filter fields (dept_slugs, document_type_slugs, keywords) execute against the
-- corpus; the inference fields are stored for downstream LLM phases.
CREATE TABLE IF NOT EXISTS categories (
    id                            INTEGER PRIMARY KEY,   -- epoch-ms, assigned in Python
    created_at                    TEXT,
    updated_at                    TEXT,
    status                        TEXT DEFAULT 'draft',  -- draft | published | archived
    slug                          TEXT,                  -- lowercase_underscores identifier/name
    owner_email                   TEXT,
    description                   TEXT,
    dept_slugs                    TEXT,
    include_child_orgs            INTEGER DEFAULT 0,     -- expand orgs to child departments
    document_type_slugs           TEXT,
    keywords                      TEXT,
    inclusion_context             TEXT,
    exclusion_context             TEXT,
    adjudication_hints_keep       TEXT,
    adjudication_hints_drop       TEXT,
    extra_guidance_urls           TEXT,
    only_use_extra_guidance_urls  INTEGER DEFAULT 0,
    extra_law_urls                TEXT,
    only_use_extra_law_urls       INTEGER DEFAULT 0
);
