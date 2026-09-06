"""Utility functions for crawlers: HTML parsing, logging, progress tracking."""
import logging
import sys
from html.parser import HTMLParser
from datetime import datetime
from pathlib import Path
from crawlers.config import LOG_DIR, REPORT_DIR

# Ensure log and report directories exist
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)

class HTMLToTextParser(HTMLParser):
    """Convert HTML to plain text, removing tags and scripts."""

    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_content = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'meta', 'link'):
            self.skip_content = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip_content = False
        elif tag in ('p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.text.append('\n')

    def handle_data(self, data):
        if not self.skip_content:
            text = data.strip()
            if text:
                self.text.append(text + ' ')

    def get_text(self):
        return ''.join(self.text).strip()

def html_to_text(html: str) -> str:
    """Convert HTML body to plain text."""
    if not html:
        return ""
    try:
        parser = HTMLToTextParser()
        parser.feed(html)
        text = parser.get_text()
        # Clean up multiple whitespace
        text = ' '.join(text.split())
        return text
    except Exception as e:
        logging.warning(f"Error parsing HTML: {e}")
        return html  # Fallback to raw HTML if parsing fails

def setup_logger(name: str, log_file: str = None) -> logging.Logger:
    """Set up a logger with file and console handlers."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    return logger

class ProgressTracker:
    """Track and report progress during crawling."""

    def __init__(self, total: int = None, report_interval: int = 100):
        self.total = total
        self.report_interval = report_interval
        self.processed = 0
        self.success = 0
        self.failed = 0
        self.start_time = datetime.now()
        self.logger = logging.getLogger(__name__)

    def increment(self, success: bool = True):
        """Increment progress counter."""
        self.processed += 1
        if success:
            self.success += 1
        else:
            self.failed += 1

        if self.processed % self.report_interval == 0:
            self.report()

    def report(self):
        """Log progress report."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        rate = self.processed / elapsed if elapsed > 0 else 0

        if self.total:
            remaining = self.total - self.processed
            eta_seconds = remaining / rate if rate > 0 else 0
            eta_hours = eta_seconds / 3600

            self.logger.info(
                f"Progress: {self.processed:,} / {self.total:,} "
                f"({100*self.processed/self.total:.1f}%) | "
                f"Success: {self.success:,} | Failed: {self.failed:,} | "
                f"Rate: {rate:.2f} items/sec | ETA: {eta_hours:.1f} hours"
            )
        else:
            self.logger.info(
                f"Progress: {self.processed:,} | "
                f"Success: {self.success:,} | Failed: {self.failed:,} | "
                f"Rate: {rate:.2f} items/sec"
            )

    def summary(self) -> dict:
        """Return summary statistics."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        rate = self.processed / elapsed if elapsed > 0 else 0

        return {
            'processed': self.processed,
            'success': self.success,
            'failed': self.failed,
            'elapsed_seconds': elapsed,
            'rate_per_sec': rate,
            'start_time': self.start_time.isoformat(),
            'end_time': datetime.now().isoformat()
        }

def generate_report(
    stage: str,
    urls_discovered: int,
    urls_fetched: int,
    urls_failed: int,
    database_size_mb: float = None,
    notes: str = ""
) -> str:
    """Generate a markdown report of the crawl run."""
    report = f"""# Crawl Run Report

**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Stage:** {stage}

## Statistics

| Metric | Value |
|--------|-------|
| URLs Discovered | {urls_discovered:,} |
| URLs Fetched | {urls_fetched:,} |
| URLs Failed | {urls_failed:,} |
| Success Rate | {100*urls_fetched/(urls_discovered or 1):.2f}% |
| Failure Rate | {100*urls_failed/(urls_discovered or 1):.2f}% |
"""

    if database_size_mb:
        report += f"| Database Size | {database_size_mb:.2f} MB |\n"

    report += f"""
## Notes

{notes or "No notes"}

---
*Report generated by gov-uk-corpus crawler*
"""
    return report

def save_report(stage: str, report_content: str):
    """Save report to file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path(REPORT_DIR) / f"report_{timestamp}_{stage}.md"
    report_path.write_text(report_content)
    return str(report_path)
