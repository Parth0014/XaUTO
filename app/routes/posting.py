from fastapi import APIRouter

from app.database import get_db_client
from app.services.posting_service import run_posting_cycle

router = APIRouter()


@router.post("/post/run")
def run_posting():
    db = get_db_client()
    return run_posting_cycle(db)
