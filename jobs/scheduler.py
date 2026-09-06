"""APScheduler setup for background jobs."""
import sys
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

sys.path.insert(0, '/Users/tomunderwood/AI Brain/gov-uk-corpus')

logger = logging.getLogger(__name__)

def create_scheduler() -> BackgroundScheduler:
    """Create and configure the background scheduler."""
    scheduler = BackgroundScheduler()

    # Job 1: Discover URLs daily (1am UTC)
    scheduler.add_job(
        func='jobs.tasks:run_discover_urls',
        trigger=CronTrigger(hour=1, minute=0),
        id='discover_urls_daily',
        name='Daily URL Discovery',
        replace_existing=True,
        max_instances=1
    )

    # Job 2: Enrich metadata in batches (every 6 hours)
    scheduler.add_job(
        func='jobs.tasks:run_enrich_batch',
        trigger=CronTrigger(hour='0,6,12,18', minute=0),
        id='enrich_metadata_batch',
        name='Metadata Enrichment Batch',
        replace_existing=True,
        max_instances=1
    )

    # Job 3: Retry failed URLs (weekly, 3am UTC)
    scheduler.add_job(
        func='jobs.tasks:retry_failed_urls',
        trigger=CronTrigger(day_of_week='0', hour=3, minute=0),
        id='retry_failed_weekly',
        name='Retry Failed URLs',
        replace_existing=True,
        max_instances=1
    )

    # Job 4: Generate weekly report (7am UTC Sunday)
    scheduler.add_job(
        func='jobs.tasks:generate_weekly_report',
        trigger=CronTrigger(day_of_week='6', hour=7, minute=0),
        id='weekly_report',
        name='Generate Weekly Report',
        replace_existing=True,
        max_instances=1
    )

    return scheduler

def start_scheduler():
    """Start the scheduler."""
    scheduler = create_scheduler()
    try:
        scheduler.start()
        logger.info("Scheduler started")
        logger.info(f"Jobs scheduled: {[job.name for job in scheduler.get_jobs()]}")
        return scheduler
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")
        raise

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    scheduler = start_scheduler()

    # Keep running
    try:
        while True:
            pass
    except KeyboardInterrupt:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
