from fastapi import APIRouter, Depends, Query

from app.database import get_db
from app.services.embedding_pipeline import backfill_embeddings

router = APIRouter()


@router.post("/embeddings/backfill")
def backfill(limit: int = Query(200, ge=1, le=1000), db=Depends(get_db)):
    inserted = backfill_embeddings(db, limit=limit)
    return {"embedded": inserted}
