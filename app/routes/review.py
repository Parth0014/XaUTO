from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pymongo import ReturnDocument

from app.database import get_db
from app.services.mongo_utils import serialize_doc, serialize_docs, to_object_id

router = APIRouter()


@router.get("/review/pending")
def list_pending(limit: int = Query(20, ge=1, le=200), db=Depends(get_db)):
    posts = list(
        db.generated_posts
        .find({"status": {"$in": ["generated", "pending"]}})
        .sort("created_at", -1)
        .limit(limit)
    )
    return serialize_docs(posts)


@router.post("/review/approve/{post_id}")
def approve(post_id: str, db=Depends(get_db)):
    oid = to_object_id(post_id)
    if not oid:
        raise HTTPException(status_code=404, detail="Generated post not found")

    updated = db.generated_posts.find_one_and_update(
        {"_id": oid},
        {"$set": {"status": "approved", "reviewed_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Generated post not found")

    return serialize_doc(updated)


@router.post("/review/reject/{post_id}")
def reject(
    post_id: str,
    payload: dict = Body(default={}),
    db=Depends(get_db),
):
    oid = to_object_id(post_id)
    if not oid:
        raise HTTPException(status_code=404, detail="Generated post not found")

    reason = None
    if isinstance(payload, dict):
        reason = payload.get("reason")

    update = {
        "status": "rejected",
        "reviewed_at": datetime.now(timezone.utc),
    }
    if reason:
        update["reject_reason"] = reason

    updated = db.generated_posts.find_one_and_update(
        {"_id": oid},
        {"$set": update},
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Generated post not found")

    return serialize_doc(updated)
