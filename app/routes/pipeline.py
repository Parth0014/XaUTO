from fastapi import APIRouter, Depends, HTTPException

from app.database import get_db
from app.services.pipeline_service import run_full_pipeline

router = APIRouter()


@router.post("/pipeline/run")
def run_pipeline(db=Depends(get_db)):
    try:
        return run_full_pipeline(db, embed_inline=False, allow_manual_posting=False)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
