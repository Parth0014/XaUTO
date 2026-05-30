from fastapi import APIRouter

from app.services.chrome_debug import ensure_chrome_debug_browser

router = APIRouter()


@router.get("/browser/chrome-debug")
def chrome_debug():
    return ensure_chrome_debug_browser()
