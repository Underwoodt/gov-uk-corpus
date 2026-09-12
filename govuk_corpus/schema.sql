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
