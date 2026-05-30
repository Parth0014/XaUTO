import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.scraper.x_scraper import scrape_x_trends
from app.database import get_db_client
from app.services.embedding_pipeline import backfill_embeddings
from app.services.cleanup_service import run_cleanup
from app.services.posting_service import run_posting_cycle
from app.services.trend_service import run_trend_pipeline
from app.services.scoring_service import score_unscored_posts

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
            scrape_x_trends,
            "interval",
            minutes=scrape_interval_minutes
        )

        scheduler.add_job(
            _run_embedding_backfill,
            "interval",
            minutes=20
        )

        scheduler.add_job(
            _run_trend_pipeline,
            "interval",
            minutes=60
        )

        scheduler.add_job(
            _run_scoring_pipeline,
            "interval",
            minutes=10
        )

        scheduler.add_job(
            _run_posting_pipeline,
            "interval",
            minutes=15
        )

        scheduler.add_job(
            _run_cleanup,
            "interval",
            hours=6
        )

    scheduler.start()


def stop_scheduler():

    if scheduler.running:
        scheduler.shutdown(wait=False)


def _run_embedding_backfill():
    db = get_db_client()
    backfill_embeddings(db, limit=200)


def _run_trend_pipeline():
    db = get_db_client()
    run_trend_pipeline(db, window_hours=6, min_posts=25)


def _run_scoring_pipeline():
    db = get_db_client()
    score_unscored_posts(db, limit=50)


def _run_posting_pipeline():
    db = get_db_client()
    run_posting_cycle(db)


def _run_cleanup():
    db = get_db_client()
    run_cleanup(db)