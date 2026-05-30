from fastapi import APIRouter, Depends

from app.database import get_db
from app.services.analytics_service import (
    get_top_posts,
    get_top_topics,
    get_sentiment_distribution,
)
from app.services.mongo_utils import serialize_docs

router = APIRouter()


@router.get("/analytics/top-posts")
def top_posts(db=Depends(get_db)):

    posts = get_top_posts(db)
    return serialize_docs(posts)


@router.get("/analytics/topics")
def topics(db=Depends(get_db)):

    return get_top_topics(db)


@router.get("/analytics/sentiment")
def sentiments(db=Depends(get_db)):

    return get_sentiment_distribution(db)