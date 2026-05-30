import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import get_db_client
from app.services.cleanup_service import run_cleanup
from app.services.pipeline_service import run_full_pipeline
from app.services.trend_service import run_trend_pipeline
from app.services.post_feedback import fetch_post_metrics

scheduler = BackgroundScheduler()


def _scheduler_enabled() -> bool:
    value = os.getenv("ENABLE_SCHEDULER", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}

def start_scheduler():

    if not _scheduler_enabled():
        return

    if scheduler.running:
        return

    if not scheduler.get_jobs():

        scrape_interval_minutes = int(
            os.getenv("X_SCRAPE_INTERVAL_MINUTES", "120")
        )

        scheduler.add_job(
            _run_full_pipeline,
            "interval",
            minutes=scrape_interval_minutes,
            max_instances=1,
            coalesce=True,
        )

        scheduler.add_job(
            _run_trend_pipeline,
            "interval",
            minutes=60,
            max_instances=1,
            coalesce=True,
        )

        scheduler.add_job(
            _run_cleanup,
            "interval",
            hours=6,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            _run_post_feedback,
            "interval",
            minutes=30,
            max_instances=1,
            coalesce=True,
        )

    scheduler.start()


def stop_scheduler():

    if scheduler.running:
        scheduler.shutdown(wait=False)


def _run_trend_pipeline():
    db = get_db_client()
    run_trend_pipeline(db, window_hours=6, min_posts=25)


def _run_full_pipeline():
    db = get_db_client()
    run_full_pipeline(db, embed_inline=False, allow_manual_posting=False)


def _run_post_feedback():
    db = get_db_client()
    fetch_post_metrics(db, limit=200)


def _run_cleanup():
    db = get_db_client()
    run_cleanup(db)