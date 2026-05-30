from fastapi import APIRouter, HTTPException

from app.database import get_db_client
from app.services.posting_service import run_posting_cycle

router = APIRouter()


@router.post("/post/run")
def run_posting():
    db = get_db_client()
    try:
        return run_posting_cycle(db)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
