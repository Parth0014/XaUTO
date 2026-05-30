from fastapi import APIRouter, Depends, HTTPException

from app.database import get_db
from app.services.posting_service import run_posting_cycle
from app.services.post_feedback import fetch_post_metrics

router = APIRouter()


@router.post("/post/run")
def run_posting(allow_manual: bool = True, db=Depends(get_db)):
    try:
        return run_posting_cycle(db, allow_manual=allow_manual, require_approval=allow_manual)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/post/feedback")
def run_feedback(db=Depends(get_db)):
    try:
        updated = fetch_post_metrics(db, limit=200)
        return {"updated": updated}
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
