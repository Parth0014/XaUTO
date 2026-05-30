import threading

from fastapi import APIRouter

from app.services.chrome_debug import ensure_chrome_debug_browser
from app.services.scrape_progress import (
    fail_scrape_progress,
    finish_scrape_progress,
    get_scrape_progress,
    reset_scrape_progress,
    start_scrape_progress,
)
from app.scraper.x_scraper import scrape_x_trends

router = APIRouter()


@router.get("/scrape/x")
def scrape_x():

    chrome_result = ensure_chrome_debug_browser()
    start_scrape_progress(chrome_result["message"])

    def run_scraper():
        try:
            scrape_x_trends()
            finish_scrape_progress("Scraping finished successfully.")
        except Exception as error:
            fail_scrape_progress(str(error))
            print("SCRAPER ERROR:", error)

    scraper_thread = threading.Thread(target=run_scraper, daemon=True)
    scraper_thread.start()

    return {
        "status": "success",
        "message": "X scraping started",
        "chrome": chrome_result,
    }


@router.get("/browser/chrome-debug")
def start_chrome_debug():

    return ensure_chrome_debug_browser()


@router.get("/scrape/status")
def scrape_status():

    return get_scrape_progress()


@router.post("/scrape/reset-status")
def reset_scrape_status():

    reset_scrape_progress()

    return get_scrape_progress()