from __future__ import annotations

from app.services.event_broadcaster import make_event, publish_sync
from app.scraper.x_scraper import scrape_x_trends
from app.services.embedding_pipeline import backfill_embeddings
from app.services.posting_service import run_posting_cycle
from app.services.scrape_progress import update_scrape_progress
from app.services.scoring_service import score_unscored_posts


def _emit_pipeline(stage: str, status: str, message: str, **extra):
    try:
        publish_sync(make_event("pipeline", {"stage": stage, "status": status, "message": message, **extra}))
    except Exception:
        pass


def run_full_pipeline(
    db,
    embed_inline: bool = False,
    allow_manual_posting: bool = False,
) -> dict:
    try:
        _emit_pipeline("scrape", "start", "Starting scrape phase.")
        update_scrape_progress(
            state="running",
            message="Pipeline started: scraping from X API.",
            last_error=None,
        )
        scrape_result = scrape_x_trends(db, embed_inline=embed_inline)
        if isinstance(scrape_result, dict) and scrape_result.get("skipped"):
            _emit_pipeline("scrape", "skipped", "Scraper already running; pipeline halted.")
            update_scrape_progress(
                state="idle",
                message="Pipeline skipped: scraper already running.",
                last_error=None,
            )
            return {
                "scrape": "skipped",
                "embedded": 0,
                "scoring": {"scored": 0},
                "posting": {"posted": 0, "reason": "scraper_running"},
            }

        _emit_pipeline("scrape", "complete", "Scrape phase complete.", result=scrape_result)
        _emit_pipeline("preprocess", "start", "Starting preprocessing and embedding backfill.")
        update_scrape_progress(
            state="running",
            message="Pipeline step: embedding backfill.",
            last_error=None,
        )
        embedded = backfill_embeddings(db, limit=200)
        _emit_pipeline("preprocess", "complete", "Preprocessing and embedding backfill complete.", embedded=embedded)

        _emit_pipeline("score", "start", "Starting scoring phase.")
        update_scrape_progress(
            state="running",
            message="Pipeline step: scoring generated posts.",
            last_error=None,
        )
        scored = score_unscored_posts(db, limit=50)
        _emit_pipeline("score", "complete", "Scoring phase complete.", scored=scored.get("scored", 0))

        _emit_pipeline("post", "start", "Starting posting phase.")
        update_scrape_progress(
            state="running",
            message="Pipeline step: posting to X API.",
            last_error=None,
        )
        posting = run_posting_cycle(
            db,
            allow_manual=allow_manual_posting,
            require_approval=allow_manual_posting,
        )
        _emit_pipeline("post", "complete", "Posting phase complete.", posting=posting)

        update_scrape_progress(
            state="idle",
            message="Pipeline completed successfully.",
            last_error=None,
        )
        _emit_pipeline("complete", "complete", "Pipeline completed successfully.")

        return {
            "scrape": "ok",
            "embedded": embedded,
            "scoring": scored,
            "posting": posting,
        }
    except Exception as error:
        _emit_pipeline("error", "failed", "Pipeline failed.", error=str(error))
        update_scrape_progress(
            state="error",
            message="Pipeline failed.",
            last_error=str(error),
        )
        raise
