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
    gds_english_score  REAL,                 -- GDS plain-English weighted impact (higher = worse)
    gds_findings       TEXT,                 -- readable class-level summary of the GDS issues
    gds_checks         TEXT,                 -- JSON {"words":N,"counts":{class:count}} — source of truth
    gds_stars          INTEGER,              -- 1–5 plain-English quality rating (5 healthy → 1 poor)
    source             TEXT,                 -- sitemap | attachment | redirect | seed | other
    document_type      TEXT,
    parent_document_type TEXT,     -- for html_publication: parent publication's type (from JSON)
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
    last_changed_at    TEXT,
    content_id         TEXT,                 -- GOV.UK's real unique id (many urls -> one content_id)
    view_count         INTEGER,              -- GOV.UK Search view_count (~14-day pageviews); collected after a shortlist rebuild
    view_count_updated TEXT,                 -- date (YYYY-MM-DD) the view_count above was collected
    view_count_source  TEXT                  -- 'page' (page's own count) | 'parent' (inherited from parent publication)
);
CREATE INDEX IF NOT EXISTS idx_content_document_type ON content(document_type);
CREATE INDEX IF NOT EXISTS idx_content_content_id ON content(content_id);
CREATE INDEX IF NOT EXISTS idx_content_parent_document_type ON content(parent_document_type);
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
CREATE INDEX IF NOT EXISTS idx_page_orgs_slug_url ON page_organisations(organisation_slug, page_url);

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

-- Materialised page count per organisation (distinct content_id over fetched, non-redirect
-- pages), so the org picker can order/label organisations by size without an ~18s GROUP BY on
-- every form load. Refreshed by the nightly tidy-up (and on demand).
CREATE TABLE IF NOT EXISTS organisation_page_counts (
    slug        TEXT PRIMARY KEY,
    pages       INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT
);

-- AI inclusion-pass evaluation, tracked per run so different models can be compared.
CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id       TEXT PRIMARY KEY,
    category_id  INTEGER,
    name         TEXT,             -- human label, e.g. "Test-1" (editable)
    source_run_id TEXT,            -- the run this one builds on (exclusion → its inclusion run)
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
    in_tokens    INTEGER DEFAULT 0,   -- total input tokens billed across the run (hit + miss)
    out_tokens   INTEGER DEFAULT 0,   -- total output tokens billed across the run
    hit_tokens   INTEGER DEFAULT 0,   -- input tokens served from cache (cache-hit rate)
    miss_tokens  INTEGER DEFAULT 0,   -- input tokens NOT from cache (cache-miss rate)
    total_ms     INTEGER DEFAULT 0,
    run_status   TEXT,                 -- 'running' | 'stopped' | 'complete' (NULL = legacy/unknown)
    pid          INTEGER,              -- OS pid of the driver process while running
    host         TEXT,                 -- hostname of that process
    heartbeat_at TEXT,                 -- last time the driver made progress
    prompt_spec  TEXT                  -- JSON snapshot of the prompt inputs the run used (reproducible)
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_cat ON evaluation_runs(category_id);

CREATE TABLE IF NOT EXISTS evaluation_results (
    run_id       TEXT NOT NULL,
    category_id  INTEGER,
    url          TEXT NOT NULL,
    keep         INTEGER,
    score        REAL,
    reason       TEXT,
    raw_reply    TEXT,          -- the model's raw reply, kept verbatim for debugging (esp. unparseable ones)
    primary_topic TEXT,         -- inclusion pass: what the page is mainly about (<=10-word noun phrase); fed to Phase 2 as {{PASS1_TOPIC}}
    where_hit    TEXT,          -- inclusion pass: JSON list of the fields the topic appeared in (title | description | body)
    evidence     TEXT,          -- inclusion pass: JSON list of verbatim quotes grounding the decision
    content_hash TEXT,          -- content.content_hash of the body actually evaluated (drift detection vs gold labels)
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
    slug                          TEXT,                  -- identifier/name: lowercase, digits, hyphens/underscores
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
    only_use_extra_law_urls       INTEGER DEFAULT 0,
    should_include_urls           TEXT,   -- URLs expected IN the final shortlist (URL-check list)
    should_exclude_urls           TEXT,   -- URLs expected OUT of the shortlist (for later checks)
    hybrid_on_save                INTEGER DEFAULT 0   -- run GOV.UK hybrid search (bg) on save
);

-- Precomputed "input shortlist" size per category (organisations + document
-- types, no keywords). Refreshed as the tidy-up phase of the nightly corpus
-- cycle (run_all) and on every category create/edit, so the Categories list
-- reads a stored number instead of running a live corpus COUNT per row.
CREATE TABLE IF NOT EXISTS category_page_counts (
    category_id  INTEGER PRIMARY KEY,
    pages_kept   INTEGER,
    computed_at  TEXT
);

-- Persisted membership of each category's deterministic shortlist (organisation +
-- document-type + keyword filters), one row per distinct page. content_id is GOV.UK's
-- real unique id (url fallback when absent); url is the representative MIN(url).
CREATE TABLE IF NOT EXISTS category_shortlist_pages (
    category_id      INTEGER NOT NULL,
    content_id       TEXT    NOT NULL,
    url              TEXT,
    matched_keywords TEXT,   -- JSON array of the category keywords this page matched (corpus/deterministic)
    computed_at      TEXT,
    PRIMARY KEY (category_id, content_id)
);
CREATE INDEX IF NOT EXISTS idx_csp_category ON category_shortlist_pages(category_id);

-- On-demand GOV.UK Search coverage: augmented shortlist with provenance vs GOV.UK Search.
-- source is 'shortlister' | 'both' | 'search' (search-only may not be in our corpus, so
-- content_id may be NULL). Separate from category_shortlist_pages so save-rebuild won't wipe it.
CREATE TABLE IF NOT EXISTS category_search_pages (
    category_id    INTEGER NOT NULL,
    url            TEXT    NOT NULL,
    content_id     TEXT,
    title          TEXT,
    document_type  TEXT,
    source         TEXT,
    phrases        TEXT,          -- keywords that matched in GOV.UK Search (comma-joined)
    corpus_phrases TEXT,          -- JSON array of the keywords this page matched in our corpus (shortlister/both)
    es_score       REAL,          -- GOV.UK Search relevance score (best across phrases); NULL if not returned by GOV.UK
    computed_at    TEXT,
    PRIMARY KEY (category_id, url)
);
CREATE INDEX IF NOT EXISTS idx_csrch_category ON category_search_pages(category_id, source);

-- Reporting view: each category's materialised shortlist joined to page attributes.
CREATE VIEW IF NOT EXISTS category_shortlist_report AS
SELECT m.category_id, m.content_id, m.url, m.computed_at,
       c.title, c.document_type,
       CASE WHEN c.document_type = 'html_publication'
            THEN COALESCE(NULLIF(c.parent_document_type, ''), c.document_type)
            ELSE c.document_type END AS effective_document_type,
       c.public_updated_at, c.first_published_at,
       c.reading_age, c.gds_stars, c.gds_english_score,
       (SELECT po.organisation_slug FROM page_organisations po
        WHERE po.page_url = c.url AND po.role = 'primary' LIMIT 1) AS primary_org
FROM category_shortlist_pages m
JOIN content c ON c.url = m.url;

-- Per-page feedback (guc-0019): a rating + comment left from the feedback widget on any page.
CREATE TABLE IF NOT EXISTS page_feedback (
    id               INTEGER PRIMARY KEY,
    page_id          TEXT,
    page_title       TEXT,
    feedback_text    TEXT,
    q_functionality  INTEGER,   -- "does this page do what you need?"  (1-5)
    q_ease           INTEGER,   -- "is it easy to use?"                (1-5)
    q_quality        INTEGER,   -- "quality of the results / output?"  (1-5)
    created_at       TEXT,
    created_by       TEXT,
    created_by_email TEXT
);

-- Gold labels for the pipeline quality benchmark: a human's verdict per page, in / out /
-- borderline, with a one-line rationale. The stratum_* fields record how the page was sampled
-- (score band, source, doc type) and content_hash_at_label the page body the labeller saw, so
-- drift between labelling and evaluation is detectable. Managed by govuk_corpus/gold.py.
CREATE TABLE IF NOT EXISTS category_gold_labels (
    category_id           INTEGER NOT NULL,
    url                   TEXT NOT NULL,      -- canonicalised
    content_id            TEXT,
    label                 TEXT NOT NULL,      -- in | out | borderline
    rationale             TEXT,
    labelled_by           TEXT,
    labelled_at           TEXT,
    content_hash_at_label TEXT,
    stratum_score_band    TEXT,               -- 0 | 0.1-0.3 | 0.4-0.6 | 0.7-1.0 | unscored (at export)
    stratum_source        TEXT,               -- both | shortlister | search | search_below_floor
    stratum_doc_type      TEXT,               -- effective document type at export
    seed_origin           TEXT,               -- should_include | should_exclude | NULL
    gold_version          INTEGER DEFAULT 1,
    PRIMARY KEY (category_id, url)
);
