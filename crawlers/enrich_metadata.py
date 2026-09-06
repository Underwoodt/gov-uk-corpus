"""Fetch metadata from gov.uk Content API and enrich URL records."""
import sys
import logging
import time
from datetime import datetime
from typing import List, Tuple, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, '/Users/tomunderwood/AI Brain/gov-uk-corpus')

from database.connection import (
    init_connection_pool,
    get_discovered_urls,
    update_url_metadata,
    log_error,
    update_crawl_run
)
from crawlers.config import (
    GOV_UK_API_BASE,
    RATE_LIMIT_PER_SEC,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF_FACTOR,
    MAX_RETRIES,
    CRAWLER_USER_AGENT,
    get_log_file
)
from crawlers.utils import setup_logger, ProgressTracker, generate_report, save_report, html_to_text

logger = None
progress = None

class RateLimitedSession:
    """Requests session with rate limiting."""

    def __init__(self, rate_per_sec: float = 2):
        self.session = self._setup_session()
        self.rate_per_sec = rate_per_sec
        self.min_interval = 1.0 / rate_per_sec
        self.last_request_time = 0

    def _setup_session(self) -> requests.Session:
        """Create a requests session with retry strategy."""
        session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({'User-Agent': CRAWLER_USER_AGENT})
        return session

    def get(self, url: str, **kwargs) -> requests.Response:
        """Get with rate limiting."""
        # Rate limit enforcement
        elapsed_since_last = time.time() - self.last_request_time
        if elapsed_since_last < self.min_interval:
            time.sleep(self.min_interval - elapsed_since_last)

        response = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        self.last_request_time = time.time()
        return response

    def close(self):
        """Close the session."""
        self.session.close()

def extract_path_from_url(url: str) -> str:
    """Extract the path component from a full URL for API lookup."""
    # URL format: https://www.gov.uk/path/to/page
    # Path for API: /path/to/page
    if url.startswith('https://www.gov.uk'):
        return url[len('https://www.gov.uk'):]
    elif url.startswith('http://www.gov.uk'):
        return url[len('http://www.gov.uk'):]
    return url

def fetch_page_metadata(session: RateLimitedSession, url: str) -> Optional[dict]:
    """Fetch page metadata from gov.uk Content API."""
    try:
        path = extract_path_from_url(url)
        api_url = f"{GOV_UK_API_BASE}{path}"

        response = session.get(api_url)
        response.raise_for_status()

        data = response.json()

        # Extract fields
        metadata = {
            'title': data.get('title', ''),
            'description': data.get('description', ''),
            'document_type': data.get('document_type', ''),
            'api_base_url': GOV_UK_API_BASE,
            'status': 'fetched'
        }

        # Extract organisations (array of dicts with 'title' field)
        orgs = data.get('organisations', [])
        if orgs:
            org_names = [org.get('title', '') if isinstance(org, dict) else str(org) for org in orgs]
            metadata['organisations'] = org_names

        # Convert HTML body to plain text
        body_html = data.get('body', '')
        if body_html:
            metadata['body_text'] = html_to_text(body_html)

        return metadata

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(f"URL not found in Content API (404): {url}")
        else:
            logger.error(f"HTTP error fetching {url}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error fetching {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error parsing response for {url}: {e}")
        return None

def enrich_batch(batch_size: int = 500):
    """Fetch metadata for a batch of discovered URLs."""
    global logger, progress

    # Setup logging
    log_file = get_log_file('enrich_metadata')
    logger = setup_logger('enrich_metadata', log_file)

    # Initialize database
    init_connection_pool()

    # Start crawl run
    run_id = update_crawl_run(start_crawl_run('metadata_enrichment'), urls_fetched=0, urls_failed=0)

    session = RateLimitedSession(rate_per_sec=RATE_LIMIT_PER_SEC)
    progress = ProgressTracker(report_interval=100)

    urls_to_process: List[Tuple[int, str]] = get_discovered_urls(limit=batch_size)
    logger.info(f"Processing batch of {len(urls_to_process)} URLs")

    try:
        for url_id, url in urls_to_process:
            try:
                metadata = fetch_page_metadata(session, url)

                if metadata:
                    update_url_metadata(url_id, metadata)
                    progress.increment(success=True)
                else:
                    log_error(url_id, "Failed to fetch from Content API")
                    progress.increment(success=False)

            except Exception as e:
                logger.error(f"Error processing URL {url_id} ({url}): {e}")
                log_error(url_id, str(e))
                progress.increment(success=False)

        # Final report
        summary = progress.summary()
        logger.info(f"Batch enrichment complete: {summary['success']} succeeded, {summary['failed']} failed")

        # Update crawl run
        update_crawl_run(
            run_id,
            urls_fetched=summary['success'],
            urls_failed=summary['failed'],
            status='completed'
        )

        # Generate report
        report = generate_report(
            stage='metadata_enrichment',
            urls_discovered=batch_size,
            urls_fetched=summary['success'],
            urls_failed=summary['failed'],
            notes=f"Batch processing completed\nRate: {summary['rate_per_sec']:.2f} req/sec"
        )
        report_path = save_report('enrich_metadata', report)
        logger.info(f"Report saved to: {report_path}")

        return True

    except Exception as e:
        logger.error(f"Fatal error in enrichment: {e}", exc_info=True)
        update_crawl_run(run_id, status='failed')
        return False
    finally:
        session.close()

if __name__ == '__main__':
    success = enrich_batch()
    sys.exit(0 if success else 1)
