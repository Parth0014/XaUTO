from fastapi import APIRouter, Depends, Query

from app.database import get_db
from app.services.mongo_utils import serialize_doc, serialize_docs, to_object_id
from app.services.trend_service import run_trend_pipeline

router = APIRouter()


@router.post("/trends/run")
def run_trends(
    window_hours: int = Query(6, ge=1, le=72),
    min_posts: int = Query(25, ge=5, le=500),
    db=Depends(get_db),
):
    return run_trend_pipeline(db, window_hours=window_hours, min_posts=min_posts)


@router.get("/trends/latest")
def latest_trends(
    limit: int = Query(5, ge=1, le=50),
    db=Depends(get_db),
):
    clusters = list(
        db.trend_clusters
        .find()
        .sort("created_at", -1)
        .limit(limit)
    )

    return serialize_docs(clusters)


@router.get("/trends/{cluster_id}/patterns")
def cluster_patterns(cluster_id: str, db=Depends(get_db)):
    oid = to_object_id(cluster_id)
    if not oid:
        return None

    pattern = db.trend_patterns.find_one({"cluster_id": oid}, sort=[("created_at", -1)])

    if not pattern:
        return None

    doc = serialize_doc(pattern)
    if doc and doc.get("cluster_id") is not None:
        doc["cluster_id"] = str(doc.get("cluster_id"))
    return doc
