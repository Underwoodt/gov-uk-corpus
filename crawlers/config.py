"""Configuration and constants for crawlers."""
import os
from datetime import datetime

# API Configuration
GOV_UK_SITEMAP_URL = "https://www.gov.uk/sitemap.xml"
GOV_UK_API_BASE = "https://www.gov.uk/api/content"

# Rate limiting (2 requests/sec to be respectful)
RATE_LIMIT_PER_SEC = 2
REQUEST_TIMEOUT = 30
RETRY_BACKOFF_FACTOR = 2
MAX_RETRIES = 3

# Batch processing
BATCH_SIZE = 1000
LOG_INTERVAL = 100

# Database
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "gov_uk_urls")
DB_USER = os.getenv("DB_USER", "gov_uk_crawler")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Logging
LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Monitoring
REPORT_DIR = os.getenv("REPORT_DIR", "./reports")
REPORT_INTERVAL = 1000  # Report progress every N URLs

# Crawler metadata
CRAWLER_USER_AGENT = "Gov-UK-Corpus-Crawler/1.0 (+https://github.com/yourusername/gov-uk-corpus)"
CRAWLER_NAME = "gov-uk-corpus"

def get_log_file(stage: str) -> str:
    """Get log file path for a crawl stage."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"run_{timestamp}_{stage}.log"
    return os.path.join(LOG_DIR, filename)

def get_report_file(run_datetime: datetime) -> str:
    """Get report file path for a run."""
    timestamp = run_datetime.strftime("%Y%m%d_%H%M%S")
    filename = f"report_{timestamp}.md"
    return os.path.join(REPORT_DIR, filename)
