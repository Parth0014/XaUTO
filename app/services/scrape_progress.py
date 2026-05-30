from datetime import datetime, timezone
from threading import Lock
from app.services.event_broadcaster import publish_sync, make_event


_lock = Lock()
_state = {
    "state": "idle",
    "message": "Waiting to start scraping.",
    "chrome": "unknown",
    "cycle": 0,
    "inserted": 0,
    "seen": 0,
    "last_topic": None,
    "last_author": None,
    "last_content": None,
    "last_error": None,
    "started_at": None,
    "updated_at": None,
}


def _stamp_state():
    _state["updated_at"] = datetime.now(timezone.utc).isoformat()


def reset_scrape_progress(message: str = "Waiting to start scraping."):
    with _lock:
        _state.update({
            "state": "idle",
            "message": message,
            "chrome": "unknown",
            "cycle": 0,
            "inserted": 0,
            "seen": 0,
            "last_topic": None,
            "last_author": None,
            "last_content": None,
            "last_error": None,
            "started_at": None,
        })
        _stamp_state()


def start_scrape_progress(chrome_message: str):
    with _lock:
        _state.update({
            "state": "running",
            "message": "Scraping in progress.",
            "chrome": chrome_message,
            "cycle": 0,
            "inserted": 0,
            "seen": 0,
            "last_topic": None,
            "last_author": None,
            "last_content": None,
            "last_error": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        _stamp_state()


def update_scrape_progress(**kwargs):
    with _lock:
        _state.update(kwargs)
        _stamp_state()
        try:
            publish_sync(make_event("scrape_progress", dict(_state)))
        except Exception:
            pass


def finish_scrape_progress(message: str = "Scraping finished."):
    with _lock:
        _state.update({
            "state": "idle",
            "message": message,
        })
        _stamp_state()


def fail_scrape_progress(error_message: str):
    with _lock:
        _state.update({
            "state": "error",
            "message": "Scraping failed.",
            "last_error": error_message,
        })
        _stamp_state()
        try:
            publish_sync(make_event("scrape_progress", dict(_state)))
        except Exception:
            pass


def get_scrape_progress():
    with _lock:
        return dict(_state)