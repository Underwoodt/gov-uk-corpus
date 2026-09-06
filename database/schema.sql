-- Gov.uk URL Corpus Database Schema
-- PostgreSQL 13+

-- Drop existing tables if they exist (for testing/reset)
DROP TABLE IF EXISTS crawl_runs CASCADE;
DROP TABLE IF EXISTS gov_uk_urls CASCADE;

-- Main URL and metadata table
CREATE TABLE gov_uk_urls (
  id SERIAL PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  sitemap_filename VARCHAR(50),
  title TEXT,
  description TEXT,
  body_text TEXT,
  api_base_url TEXT,
  document_type VARCHAR(100),
  organisations TEXT[],

  -- Metadata columns
  run_datetime TIMESTAMP,
  created_datetime TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_datetime TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  -- Status tracking
  status VARCHAR(20) DEFAULT 'discovered',
  last_error TEXT,
  attempt_count INT DEFAULT 0,

  CONSTRAINT status_check CHECK (status IN ('discovered', 'fetched', 'indexed', 'failed'))
);

-- Crawl run tracking table
CREATE TABLE crawl_runs (
  id SERIAL PRIMARY KEY,
  run_datetime TIMESTAMP NOT NULL,
  stage VARCHAR(50),
  urls_discovered INT DEFAULT 0,
  urls_fetched INT DEFAULT 0,
  urls_failed INT DEFAULT 0,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP,
  status VARCHAR(20) DEFAULT 'in_progress',

  CONSTRAINT run_status_check CHECK (status IN ('in_progress', 'completed', 'failed'))
);

-- Full-text search index
CREATE INDEX idx_gov_uk_urls_fts ON gov_uk_urls
  USING GIN(to_tsvector('english',
    COALESCE(title, '') || ' ' ||
    COALESCE(description, '') || ' ' ||
    COALESCE(body_text, '')));

-- Faceted search indexes
CREATE INDEX idx_gov_uk_urls_org ON gov_uk_urls USING GIN(organisations);
CREATE INDEX idx_gov_uk_urls_doc_type ON gov_uk_urls(document_type);
CREATE INDEX idx_gov_uk_urls_status ON gov_uk_urls(status);
CREATE INDEX idx_gov_uk_urls_url ON gov_uk_urls(url);

-- Status queries optimization
CREATE INDEX idx_gov_uk_urls_status_updated ON gov_uk_urls(status, updated_datetime);

-- Crawl runs indexes
CREATE INDEX idx_crawl_runs_stage ON crawl_runs(stage);
CREATE INDEX idx_crawl_runs_status ON crawl_runs(status);
CREATE INDEX idx_crawl_runs_run_datetime ON crawl_runs(run_datetime DESC);

-- Comments for documentation
COMMENT ON TABLE gov_uk_urls IS 'URLs from gov.uk sitemap with enriched metadata from Content API';
COMMENT ON COLUMN gov_uk_urls.status IS 'discovered = in sitemap, fetched = metadata fetched from API, indexed = ready for search, failed = API error';
COMMENT ON TABLE crawl_runs IS 'Track each crawl run: discovery stage, enrichment progress, timing';
