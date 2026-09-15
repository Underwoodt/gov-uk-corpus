-- GOV.UK corpus — Postgres schema.
-- Deliberately kept close to the SQLite pilot schema for a mechanical migration:
-- timestamps and counters are stored as text (ISO-8601 / JSON strings) and boolean
-- flags as smallint, so the backend code is identical bar the driver + placeholders.
-- (Once the corpus is stable these can be tightened to timestamptz / boolean / jsonb.)

CREATE TABLE IF NOT EXISTS runs (
    run_id      text PRIMARY KEY,
    started_at  text NOT NULL,
    finished_at text,
    status      text NOT NULL,
    stage       text,
    scope       text,
    counters    text,
    notes       text
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id          bigserial PRIMARY KEY,
    run_id      text,
    stage       text,
    url         text,
    action      text,
    http_status integer,
    bytes       integer,
    duration_ms integer,
    error       text,
    ts          text
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_run ON fetch_log(run_id);

CREATE TABLE IF NOT EXISTS content (
    url                text PRIMARY KEY,
    content            text,
    content_hash       text,
    source             text,
    document_type      text,
    schema_name        text,
    title              text,
    description        text,
    first_published_at text,
    public_updated_at  text,
    withdrawn          smallint DEFAULT 0,
    is_redirect        smallint DEFAULT 0,
    destination_url    text,
    http_status        integer,
    sitemap_lastmod    text,
    first_seen_run     text,
    last_seen_run      text,
    last_changed_run   text,
    first_seen_at      text,
    last_seen_at       text,
    last_changed_at    text
);
CREATE INDEX IF NOT EXISTS idx_content_document_type ON content(document_type);
CREATE INDEX IF NOT EXISTS idx_content_source ON content(source);

CREATE TABLE IF NOT EXISTS sitemap (
    url          text PRIMARY KEY,
    sitemap_file text,
    lastmod      text,
    imported_at  text
);

CREATE TABLE IF NOT EXISTS page_organisations (
    page_url                text NOT NULL,
    organisation_content_id text,
    organisation_slug       text,
    role                    text NOT NULL,
    PRIMARY KEY (page_url, organisation_content_id, role)
);
CREATE INDEX IF NOT EXISTS idx_page_orgs_slug ON page_organisations(organisation_slug);

CREATE TABLE IF NOT EXISTS page_links (
    parent_url text NOT NULL,
    child_url  text NOT NULL,
    relation   text NOT NULL,
    PRIMARY KEY (parent_url, child_url, relation)
);

CREATE TABLE IF NOT EXISTS redirects (
    source_url      text PRIMARY KEY,
    destination_url text,
    http_status     integer,
    resolved_run    text,
    resolved_at     text
);

-- Organisation registry + hierarchy (imported from the GOV.UK search-API org
-- aggregate). Lets selection expand a chosen organisation to its child departments
-- and resolve a page's organisation to its parent.
CREATE TABLE IF NOT EXISTS organisations (
    slug                 text PRIMARY KEY,
    title                text,
    acronym              text,
    content_id           text,
    org_type             text,
    org_state            text,
    brand                text,
    analytics_identifier text
);

CREATE TABLE IF NOT EXISTS organisation_hierarchy (
    parent_slug text NOT NULL,
    child_slug  text NOT NULL,
    PRIMARY KEY (parent_slug, child_slug)
);
CREATE INDEX IF NOT EXISTS idx_org_hier_parent ON organisation_hierarchy(parent_slug);
CREATE INDEX IF NOT EXISTS idx_org_hier_child  ON organisation_hierarchy(child_slug);

-- AI inclusion-pass evaluation, tracked per run so different models can be compared.
CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id       text PRIMARY KEY,
    category_id  bigint,
    name         text,             -- human label, e.g. "Test-1" (editable)
    source_run_id text,            -- the run this one builds on (exclusion → its inclusion run)
    phase        text DEFAULT 'Phase 1 - Inclusion',
    provider     text,
    model        text,
    actual_model text,
    started_at   text,
    finished_at  text,
    pages        integer DEFAULT 0,
    kept         integer DEFAULT 0,
    dropped      integer DEFAULT 0,
    unparseable  integer DEFAULT 0,
    cost         real DEFAULT 0,
    in_tokens    bigint DEFAULT 0,   -- total input tokens billed across the run (hit + miss)
    out_tokens   bigint DEFAULT 0,   -- total output tokens billed across the run
    hit_tokens   bigint DEFAULT 0,   -- input tokens served from cache (cache-hit rate)
    miss_tokens  bigint DEFAULT 0,   -- input tokens NOT from cache (cache-miss rate)
    total_ms     integer DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_cat ON evaluation_runs(category_id);

CREATE TABLE IF NOT EXISTS evaluation_results (
    run_id       text NOT NULL,
    category_id  bigint,
    url          text NOT NULL,
    keep         smallint,
    score        real,
    reason       text,
    ms           integer,
    created_at   text,
    PRIMARY KEY (run_id, url)
);
CREATE INDEX IF NOT EXISTS idx_eval_results_run_keep ON evaluation_results(run_id, keep);

-- AI spend ledger (one row per successful model call) for the daily budget.
CREATE TABLE IF NOT EXISTS ai_usage (
    day           text,
    created_at    text,
    cost          real,
    input_tokens  integer,
    output_tokens integer,
    kind          text
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_day ON ai_usage(day);

-- User-editable AI models (supplier + model id + prices) for the assistant/evaluation.
CREATE TABLE IF NOT EXISTS ai_models (
    id            bigint PRIMARY KEY,
    provider      text,
    model_id      text,
    input_per_m   real,   -- standard input rate = in_miss_off (USD / 1M)
    output_per_m  real,   -- standard output rate = out_off (USD / 1M)
    in_hit_off    real,   -- input, cache hit, off-peak
    in_hit_peak   real,   -- input, cache hit, peak
    in_miss_off   real,   -- input, cache miss, off-peak
    in_miss_peak  real,   -- input, cache miss, peak
    out_off       real,   -- output, off-peak
    out_peak      real,   -- output, peak
    created_at    text
);
CREATE INDEX IF NOT EXISTS idx_ai_models_provider ON ai_models(provider, model_id);

-- Small key/value app settings (e.g. which AI provider the UI uses).
CREATE TABLE IF NOT EXISTS app_settings (
    key   text PRIMARY KEY,
    value text
);

-- Per-page funnel audit for a category (starting point = organisation filter).
CREATE TABLE IF NOT EXISTS category_audit (
    category_id  bigint NOT NULL,
    url          text NOT NULL,
    outcome      text NOT NULL,   -- included | dropped: document type | dropped: keyword
    created_at   text,
    PRIMARY KEY (category_id, url)
);
CREATE INDEX IF NOT EXISTS idx_category_audit_outcome ON category_audit(category_id, outcome);

-- Saved shortlist specs ("categories") — CRUD from the Shortlist Builder UI.
CREATE TABLE IF NOT EXISTS categories (
    id                            bigint PRIMARY KEY,
    created_at                    text,
    updated_at                    text,
    status                        text DEFAULT 'draft',
    slug                          text,
    owner_email                   text,
    description                   text,
    dept_slugs                    text,
    include_child_orgs            smallint DEFAULT 0,
    document_type_slugs           text,
    keywords                      text,
    inclusion_context             text,
    exclusion_context             text,
    adjudication_hints_keep       text,
    adjudication_hints_drop       text,
    extra_guidance_urls           text,
    only_use_extra_guidance_urls  smallint DEFAULT 0,
    extra_law_urls                text,
    only_use_extra_law_urls       smallint DEFAULT 0
);

-- Keyword search (Path A): Postgres full-text over title + description + body.
-- search_text holds the HTML-stripped body (populated by the crawl and the
-- build_search_text backfill). The generated tsvector recomputes per row whenever
-- search_text changes, so keyword search starts on title+description and gains body
-- coverage as the backfill runs. GIN makes it fast.
-- NOTE: adding the STORED tsvector column rewrites the content table once (minutes on ~877k).
ALTER TABLE content ADD COLUMN IF NOT EXISTS search_text text;
ALTER TABLE content ADD COLUMN IF NOT EXISTS search_tsv tsvector
  GENERATED ALWAYS AS (
    to_tsvector('english',
      coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || coalesce(search_text, ''))
  ) STORED;
CREATE INDEX IF NOT EXISTS idx_content_search_tsv ON content USING GIN (search_tsv);

-- Readability / plain-English analysis of the body text (populated by a later job).
ALTER TABLE content ADD COLUMN IF NOT EXISTS reading_age real;          -- estimated reading age (years)
ALTER TABLE content ADD COLUMN IF NOT EXISTS gds_english_score real;    -- GDS plain-English compliance score
ALTER TABLE content ADD COLUMN IF NOT EXISTS gds_findings text;         -- readable summary of the GDS issues

-- Category display name/identifier (added after initial deploy).
ALTER TABLE categories ADD COLUMN IF NOT EXISTS slug text;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS include_child_orgs smallint DEFAULT 0;

-- Partial indexes over the "usable page" guard (is_redirect=0 AND content_hash
-- IS NOT NULL) that every shortlist count carries. Lets the total count do an
-- index-only scan and the document-type count skip the dead rows.
-- NOTE: built without CONCURRENTLY here (schema runs on startup), so this briefly
-- locks `content` while it builds — a minute or two on ~877k rows. To avoid the
-- lock, create them once by hand with CREATE INDEX CONCURRENTLY before deploying.
CREATE INDEX IF NOT EXISTS idx_content_usable
  ON content (url) WHERE is_redirect = 0 AND content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_content_usable_doctype
  ON content (document_type) WHERE is_redirect = 0 AND content_hash IS NOT NULL;

-- Run phase (added after evaluation_runs shipped). ADD COLUMN with DEFAULT backfills
-- existing runs to 'Phase 1 - Inclusion'.
ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS phase text DEFAULT 'Phase 1 - Inclusion';
ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS source_run_id text;

-- Per-run token totals (added after evaluation_runs shipped). Existing rows start at 0.
ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS in_tokens  bigint DEFAULT 0;
ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS out_tokens bigint DEFAULT 0;
ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS hit_tokens  bigint DEFAULT 0;
ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS miss_tokens bigint DEFAULT 0;

-- Tiered model pricing (added after ai_models shipped with a single input/output rate).
-- Backfill each tier from the existing standard rate; users then edit the real grid.
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS in_hit_off   real;
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS in_hit_peak  real;
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS in_miss_off  real;
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS in_miss_peak real;
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS out_off      real;
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS out_peak     real;
UPDATE ai_models SET in_miss_off  = COALESCE(in_miss_off,  input_per_m),
                     in_miss_peak = COALESCE(in_miss_peak, input_per_m),
                     in_hit_off   = COALESCE(in_hit_off,   input_per_m),
                     in_hit_peak  = COALESCE(in_hit_peak,  input_per_m),
                     out_off      = COALESCE(out_off,       output_per_m),
                     out_peak     = COALESCE(out_peak,      output_per_m)
 WHERE in_miss_off IS NULL OR out_off IS NULL;
