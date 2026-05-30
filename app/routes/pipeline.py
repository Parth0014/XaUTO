from fastapi import APIRouter, HTTPException

from app.database import get_db_client
from app.scraper.x_scraper import scrape_x_trends
from app.services.embedding_pipeline import backfill_embeddings
from app.services.posting_service import run_posting_cycle
from app.services.scrape_progress import update_scrape_progress
from app.services.scoring_service import score_unscored_posts

router = APIRouter()


@router.post("/pipeline/run")
def run_pipeline():
    db = get_db_client()

    try:
        update_scrape_progress(
            state="running",
            message="Pipeline started: scraping from X API.",
            last_error=None,
        )
        scrape_x_trends()

        update_scrape_progress(
            state="running",
            message="Pipeline step: embedding backfill.",
            last_error=None,
        )
        embedded = backfill_embeddings(db, limit=200)

        update_scrape_progress(
            state="running",
            message="Pipeline step: scoring generated posts.",
            last_error=None,
        )
        scored = score_unscored_posts(db, limit=50)

        update_scrape_progress(
            state="running",
            message="Pipeline step: posting to X API.",
            last_error=None,
        )
        posting = run_posting_cycle(db)

        update_scrape_progress(
            state="idle",
            message="Pipeline completed successfully.",
            last_error=None,
        )

        return {
            "scrape": "ok",
            "embedded": embedded,
            "scoring": scored,
            "posting": posting,
        }
    except Exception as error:
        update_scrape_progress(
            state="error",
            message="Pipeline failed.",
            last_error=str(error),
        )
        raise HTTPException(status_code=500, detail=str(error)) from error
