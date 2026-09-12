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
