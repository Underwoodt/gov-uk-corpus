# Gov.uk URL Corpus & Search API

A scalable, cost-effective system for collecting, enriching, and searching ~1M URLs from gov.uk using PostgreSQL and FastAPI.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Lightship VPS (1-2 CPU, 2GB RAM)            │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────┐      ┌────────────────┐                │
│  │  Crawlers       │◄────►│  PostgreSQL    │                │
│  │  - Discover     │      │  gov_uk_urls   │                │
│  │  - Enrich       │      │  + FTS Index   │                │
│  └─────────────────┘      └────────────────┘                │
│           ▲                                                   │
│           │ Rate: 2 req/sec                                  │
│           ▼                                                   │
│  ┌─────────────────────────────┐                            │
│  │  gov.uk Content API          │                            │
│  │  (public, no auth)           │                            │
│  └─────────────────────────────┘                            │
│                                                               │
│  ┌─────────────────┐      ┌────────────────┐                │
│  │  FastAPI        │      │  APScheduler   │                │
│  │  - Search API   │      │  - Daily jobs  │                │
│  │  - Stats        │      │  - Reports     │                │
│  └─────────────────┘      └────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Prerequisites

- Python 3.8+
- PostgreSQL 13+
- 2GB RAM minimum
- ~10GB disk space

### 2. Installation

```bash
cd /Users/tomunderwood/AI\ Brain/gov-uk-corpus

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your PostgreSQL credentials
```

### 3. Database Setup

```bash
# Create PostgreSQL database
createdb gov_uk_urls
createuser gov_uk_crawler
psql -U postgres -d gov_uk_urls -f database/schema.sql
```

### 4. Run Discovery (collect URLs)

```bash
python crawlers/discover_urls.py
# Expected: ~1M URLs found in 30 minutes
# Progress logged to: logs/run_YYYYMMDD_HHMMSS_discover_urls.log
```

### 5. Run Enrichment (fetch metadata)

```bash
python crawlers/enrich_metadata.py
# Expected: ~5.8 days for 1M URLs at 2 req/sec
# Check progress in logs/
# Rerunnable - resumes where it left off
```

### 6. Start Search API

```bash
python -m api.app
# Server starts at http://localhost:8000
# API docs: http://localhost:8000/docs
```

### 7. Start Background Scheduler (optional)

```bash
python jobs/scheduler.py
# Runs daily discovery, batch enrichment, weekly reports
```

## Usage

### Search API Examples

**Basic keyword search:**
```bash
curl "http://localhost:8000/search?q=climate+change&limit=10"
```

**Faceted search:**
```bash
curl "http://localhost:8000/search?q=climate&organisations=DEFRA&document_type=policy"
```

**Corpus statistics:**
```bash
curl "http://localhost:8000/search/stats"
```

### Monitoring Progress

**Check URL counts:**
```bash
psql -d gov_uk_urls -c "SELECT status, COUNT(*) FROM gov_uk_urls GROUP BY status;"
```

**View crawl runs:**
```bash
psql -d gov_uk_urls -c "SELECT * FROM crawl_runs ORDER BY started_at DESC LIMIT 10;"
```

**Check logs:**
```bash
ls -lh logs/
tail -f logs/run_*.log
```

**View reports:**
```bash
ls -lh reports/
cat reports/report_*.md
```

## Project Structure

```
gov-uk-corpus/
├── database/
│   ├── schema.sql          # PostgreSQL schema
│   └── connection.py       # Connection pooling & queries
├── crawlers/
│   ├── discover_urls.py    # Phase B: Sitemap discovery
│   ├── enrich_metadata.py  # Phase C: Content API fetch
│   ├── config.py           # Configuration constants
│   └── utils.py            # HTML parsing, logging
├── api/
│   ├── app.py              # FastAPI application
│   ├── routes.py           # Search endpoints
│   ├── models.py           # Pydantic models
│   └── queries.py          # Database queries
├── jobs/
│   ├── scheduler.py        # APScheduler setup
│   └── tasks.py            # Scheduled task functions
├── monitoring/             # Progress tracking (future)
├── logs/                   # Crawler logs
├── reports/                # Generated reports
├── requirements.txt        # Python dependencies
├── .env.example            # Environment template
└── README.md              # This file
```

## Key Features

### ✅ Scalable Design
- Fits on small VPS ($5-10/mo)
- PostgreSQL with full-text search index
- Rate-limited API crawling (2 req/sec)
- Batch processing to avoid memory spikes

### ✅ Rerunnable & Resumable
- Status tracking: discovered → fetched → indexed
- Can restart at any point
- Failed URLs marked and can be retried
- Progress logged to database

### ✅ Cost-Effective
- No external dependencies or services
- Public APIs only (no auth keys needed)
- ~6GB total storage (5GB for body_text)
- Minimal bandwidth

### ✅ Monitoring
- Real-time progress logging
- Generated reports (hourly, daily, weekly)
- Database statistics queryable at any time
- Search API health endpoint

## Database Schema

**Main table:** `gov_uk_urls`
- `url` - Full URL (unique)
- `title`, `description`, `body_text` - Content
- `document_type`, `organisations` - Metadata
- `status` - discovered | fetched | indexed | failed
- Full-text search index on title + description + body_text

**Tracking table:** `crawl_runs`
- Logs each crawl run (discovery, enrichment)
- Tracks progress, timing, and status

## Troubleshooting

### "Could not connect to PostgreSQL"
- Ensure PostgreSQL is running: `psql -U postgres`
- Check .env database credentials
- Verify database exists: `psql -l`

### Crawlers running slowly
- Check network: `curl https://www.gov.uk/sitemap.xml`
- Monitor CPU/RAM: `top`
- Check if rate limiting is working (should be ~2 req/sec)

### Search API returns empty results
- Ensure enrichment has completed: Check `crawl_runs` table
- Check database: `SELECT COUNT(*) FROM gov_uk_urls WHERE body_text IS NOT NULL;`
- Verify full-text index: `SELECT COUNT(*) FROM gov_uk_urls WHERE to_tsvector('english', body_text) @@ plainto_tsquery('english', 'climate');`

### High disk usage
- Body text is ~5KB per URL, expected ~5GB for 1M URLs
- Consider: selective indexing, compression, or partitioning for scale

## Cost Analysis

| Component | Est. Monthly |
|-----------|--------------|
| VPS (1-2 CPU, 2GB RAM) | $5-10 |
| PostgreSQL (included) | Included |
| Bandwidth (API) | Free (local) |
| **Total** | **$5-10** |

## Future Enhancements

- [ ] Document caching (ETag support)
- [ ] Incremental updates (change detection)
- [ ] Search result caching/ranking
- [ ] Data export (CSV, JSON)
- [ ] Web UI for searching and analytics
- [ ] Alert system for failed URLs
- [ ] Distributed crawling across multiple VPS instances

## License

MIT - Feel free to use and modify

## Support

For issues or questions:
1. Check logs in `logs/` directory
2. Review PostgreSQL logs
3. Test API endpoints manually with curl
4. Check GitHub issues if applicable

---

**Last Updated:** 2025-09-06
**Status:** Production-Ready
**Scale:** 1M+ URLs, full-text search, 2 req/sec rate limit
