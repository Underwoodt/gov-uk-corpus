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

-- Category display name/identifier (added after initial deploy).
ALTER TABLE categories ADD COLUMN IF NOT EXISTS slug text;
