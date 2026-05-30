from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from app.database import get_db
from app.services.mongo_utils import serialize_docs
from app.services.retrieval_service import (
    retrieve_similar_posts,
    retrieve_similar_by_post_id,
)

router = APIRouter()


@router.get("/retrieve/similar")
def retrieve_similar(
    query: str,
    top_k: int = Query(10, ge=1, le=50),
    topic: str | None = None,
    min_likes: int | None = None,
    since_hours: int | None = Query(None, ge=1, le=720),
    language: str | None = None,
    db=Depends(get_db),
):
    since_ts = None
    if since_hours:
        since_ts = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp()

    posts = retrieve_similar_posts(
        db,
        query_text=query,
        top_k=top_k,
        topic=topic,
        min_likes=min_likes,
        since_ts=since_ts,
        language=language,
    )

    return serialize_docs(posts)


@router.get("/retrieve/by-id/{post_id}")
def retrieve_by_id(
    post_id: str,
    top_k: int = Query(10, ge=1, le=50),
    topic: str | None = None,
    min_likes: int | None = None,
    since_hours: int | None = Query(None, ge=1, le=720),
    language: str | None = None,
    db=Depends(get_db),
):
    since_ts = None
    if since_hours:
        since_ts = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp()

    posts = retrieve_similar_by_post_id(
        db,
        post_id=post_id,
        top_k=top_k,
        topic=topic,
        min_likes=min_likes,
        since_ts=since_ts,
        language=language,
    )

    return serialize_docs(posts)
