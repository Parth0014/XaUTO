from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import get_db
from app.services.mongo_utils import to_object_id
from app.services.scoring_service import score_generated_post, score_unscored_posts

router = APIRouter()


@router.post("/score/run")
def run_scoring(limit: int = Query(50, ge=1, le=500), db=Depends(get_db)):
    return score_unscored_posts(db, limit=limit)


@router.post("/score/{post_id}")
def score_one(post_id: str, db=Depends(get_db)):
    oid = to_object_id(str(post_id))
    if not oid:
        raise HTTPException(status_code=404, detail="Generated post not found")

    post = db.generated_posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Generated post not found")
    return score_generated_post(db, post)
