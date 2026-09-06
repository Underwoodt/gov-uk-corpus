"""Discover all URLs from gov.uk sitemap."""
import sys
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Set
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, '/Users/tomunderwood/AI Brain/gov-uk-corpus')

from database.connection import init_connection_pool, batch_insert_urls, start_crawl_run, update_crawl_run
from crawlers.config import (
    GOV_UK_SITEMAP_URL,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF_FACTOR,
    MAX_RETRIES,
    CRAWLER_USER_AGENT,
    get_log_file
)
from crawlers.utils import setup_logger, ProgressTracker, generate_report, save_report

logger = None
progress = None

def setup_session() -> requests.Session:
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

def fetch_xml(session: requests.Session, url: str) -> str:
    """Fetch XML content from URL with error handling."""
    try:
        logger.info(f"Fetching: {url}")
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch {url}: {e}")
        raise

def extract_sitemap_urls(xml_content: str) -> List[str]:
    """Extract sitemap URLs from main sitemap.xml."""
    try:
        root = ET.fromstring(xml_content)
        # Handle namespace
        ns = {'': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        sitemaps = root.findall('.//sitemap/loc', ns)
        if not sitemaps:
            sitemaps = root.findall('.//sitemap/loc')

        urls = [elem.text for elem in sitemaps if elem.text]
        logger.info(f"Found {len(urls)} sitemap files")
        return urls
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML: {e}")
        raise

def extract_page_urls(xml_content: str) -> List[str]:
    """Extract page URLs from a sitemap file."""
    try:
        root = ET.fromstring(xml_content)
        # Handle namespace
        ns = {'': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        urls = root.findall('.//url/loc', ns)
        if not urls:
            urls = root.findall('.//url/loc')

        page_urls = [elem.text for elem in urls if elem.text]
        return page_urls
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML: {e}")
        return []

def discover_urls():
    """Main discovery process."""
    global logger, progress

    # Setup logging
    log_file = get_log_file('discover_urls')
    logger = setup_logger('discover_urls', log_file)

    # Initialize database
    init_connection_pool()

    # Start crawl run
    run_id = start_crawl_run('sitemap_discovery')
    run_datetime = datetime.now()

    session = setup_session()
    all_urls: Set[str] = set()
    failed_sitemaps = []

    try:
        # Fetch main sitemap
        logger.info(f"Starting URL discovery from {GOV_UK_SITEMAP_URL}")
        main_xml = fetch_xml(session, GOV_UK_SITEMAP_URL)

        # Extract all sitemap files
        sitemap_urls = extract_sitemap_urls(main_xml)
        progress = ProgressTracker(total=len(sitemap_urls), report_interval=1)

        # Process each sitemap
        for sitemap_url in sitemap_urls:
            try:
                sitemap_xml = fetch_xml(session, sitemap_url)
                page_urls = extract_page_urls(sitemap_xml)

                # Extract filename
                sitemap_filename = sitemap_url.split('/')[-1]

                # Batch insert URLs
                new_urls = [url for url in page_urls if url not in all_urls]
                if new_urls:
                    batch_insert_urls(new_urls, sitemap_filename, run_datetime)
                    all_urls.update(new_urls)

                progress.increment(success=True)

            except Exception as e:
                logger.error(f"Error processing sitemap {sitemap_url}: {e}")
                failed_sitemaps.append(sitemap_url)
                progress.increment(success=False)

        # Final report
        summary = progress.summary()
        logger.info(f"Discovery complete: {len(all_urls):,} unique URLs found")
        logger.info(f"Failed sitemaps: {len(failed_sitemaps)}")

        # Update crawl run
        update_crawl_run(run_id, urls_discovered=len(all_urls), status='completed')

        # Generate report
        report = generate_report(
            stage='sitemap_discovery',
            urls_discovered=len(all_urls),
            urls_fetched=0,
            urls_failed=len(failed_sitemaps),
            notes=f"Processed {len(sitemap_urls)} sitemap files\nFailed: {failed_sitemaps[:5]}"
        )
        report_path = save_report('discover_urls', report)
        logger.info(f"Report saved to: {report_path}")

        return True

    except Exception as e:
        logger.error(f"Fatal error in discovery: {e}", exc_info=True)
        update_crawl_run(run_id, status='failed')
        return False
    finally:
        session.close()

if __name__ == '__main__':
    success = discover_urls()
    sys.exit(0 if success else 1)
