"""Scheduled task functions."""
import sys
import subprocess
import logging
from datetime import datetime, timedelta

sys.path.insert(0, '/Users/tomunderwood/AI Brain/gov-uk-corpus')

from database.connection import init_connection_pool, get_connection, update_url_metadata
from crawlers.config import get_log_file
from crawlers.utils import setup_logger, generate_report, save_report

logger = logging.getLogger(__name__)

def run_discover_urls():
    """Run URL discovery task."""
    logger.info("Starting scheduled URL discovery...")
    try:
        result = subprocess.run(
            [sys.executable, '/Users/tomunderwood/AI Brain/gov-uk-corpus/crawlers/discover_urls.py'],
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )
        if result.returncode == 0:
            logger.info("URL discovery completed successfully")
        else:
            logger.error(f"URL discovery failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("URL discovery timed out after 1 hour")
    except Exception as e:
        logger.error(f"Error running URL discovery: {e}")

def run_enrich_batch():
    """Run metadata enrichment batch task."""
    logger.info("Starting scheduled metadata enrichment...")
    try:
        result = subprocess.run(
            [sys.executable, '/Users/tomunderwood/AI Brain/gov-uk-corpus/crawlers/enrich_metadata.py'],
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )
        if result.returncode == 0:
            logger.info("Metadata enrichment completed successfully")
        else:
            logger.error(f"Metadata enrichment failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("Metadata enrichment timed out after 1 hour")
    except Exception as e:
        logger.error(f"Error running metadata enrichment: {e}")

def retry_failed_urls():
    """Retry URLs that previously failed."""
    logger.info("Starting retry of failed URLs...")
    try:
        init_connection_pool()

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get URLs that failed and haven't exceeded max retries
                cur.execute("""
                    SELECT id, url FROM gov_uk_urls
                    WHERE status = 'failed' AND attempt_count < 3
                    ORDER BY updated_datetime ASC
                    LIMIT 100
                """)

                failed_urls = cur.fetchall()
                logger.info(f"Retrying {len(failed_urls)} failed URLs")

                # This is a simplified retry - in production you'd call enrich_metadata
                # for these specific URLs
                for url_id, url in failed_urls:
                    cur.execute(
                        "UPDATE gov_uk_urls SET status = %s WHERE id = %s",
                        ('discovered', url_id)
                    )

        logger.info(f"Reset {len(failed_urls)} URLs to retry")

    except Exception as e:
        logger.error(f"Error in retry task: {e}")

def generate_weekly_report():
    """Generate weekly corpus statistics report."""
    logger.info("Generating weekly report...")
    try:
        init_connection_pool()

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get statistics
                cur.execute("SELECT COUNT(*) FROM gov_uk_urls")
                total_urls = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM gov_uk_urls WHERE status = 'fetched'")
                fetched_urls = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM gov_uk_urls WHERE status = 'failed'")
                failed_urls = cur.fetchone()[0]

                # Top document types
                cur.execute("""
                    SELECT document_type, COUNT(*) as cnt
                    FROM gov_uk_urls
                    WHERE document_type IS NOT NULL
                    GROUP BY document_type
                    ORDER BY cnt DESC
                    LIMIT 10
                """)
                doc_types = cur.fetchall()

                # Top organisations
                cur.execute("""
                    SELECT UNNEST(organisations) as org, COUNT(*) as cnt
                    FROM gov_uk_urls
                    WHERE organisations IS NOT NULL
                    GROUP BY org
                    ORDER BY cnt DESC
                    LIMIT 10
                """)
                orgs = cur.fetchall()

        # Generate report
        report = f"""# Weekly Corpus Report

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Statistics

- **Total URLs:** {total_urls:,}
- **Fetched (with metadata):** {fetched_urls:,} ({100*fetched_urls/(total_urls or 1):.1f}%)
- **Failed:** {failed_urls:,} ({100*failed_urls/(total_urls or 1):.1f}%)
- **Pending:** {total_urls - fetched_urls - failed_urls:,}

## Top Document Types

| Type | Count |
|------|-------|
"""
        for doc_type, count in doc_types[:10]:
            report += f"| {doc_type} | {count:,} |\n"

        report += "\n## Top Organisations\n\n| Organisation | Count |\n|---|---|\n"
        for org, count in orgs[:10]:
            report += f"| {org} | {count:,} |\n"

        report += "\n---\n*Report generated by gov-uk-corpus scheduler*\n"

        # Save report
        report_path = save_report('weekly', report)
        logger.info(f"Weekly report saved to: {report_path}")

    except Exception as e:
        logger.error(f"Error generating weekly report: {e}")
