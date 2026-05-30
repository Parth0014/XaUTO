from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone


def _retention_days() -> int:
    return int(os.getenv("CLEANUP_RETENTION_DAYS", "30"))


def _max_scraped_posts() -> int:
    return int(os.getenv("CLEANUP_MAX_SCRAPED_POSTS", "5000"))


def _max_generated_posts() -> int:
    return int(os.getenv("CLEANUP_MAX_GENERATED_POSTS", "2000"))


def _max_trend_clusters() -> int:
    return int(os.getenv("CLEANUP_MAX_TREND_CLUSTERS", "2000"))


def _max_analytics_events() -> int:
    return int(os.getenv("CLEANUP_MAX_ANALYTICS_EVENTS", "20000"))


def _delete_older_than(db, collection: str, cutoff: datetime, date_field: str = "created_at") -> int:
    result = db[collection].delete_many({date_field: {"$lt": cutoff}})
    return int(result.deleted_count or 0)


def _cap_collection(db, collection: str, max_docs: int, date_field: str = "created_at") -> int:
    if max_docs <= 0:
        return 0

    total = db[collection].count_documents({})
    if total <= max_docs:
        return 0

    overflow = total - max_docs
    cursor = db[collection].find({}, {"_id": 1}).sort(date_field, 1).limit(overflow)
    ids = [doc.get("_id") for doc in cursor if doc.get("_id")]
    if not ids:
        return 0

    result = db[collection].delete_many({"_id": {"$in": ids}})
    return int(result.deleted_count or 0)


def run_cleanup(db) -> dict:
    now = datetime.now(timezone.utc)
    retention_days = _retention_days()

    deleted = {}
    if retention_days > 0:
        cutoff = now - timedelta(days=retention_days)
        deleted = {
            "scraped_posts": _delete_older_than(db, "scraped_posts", cutoff),
            "embedding_records": _delete_older_than(db, "embedding_records", cutoff),
            "trend_clusters": _delete_older_than(db, "trend_clusters", cutoff),
            "trend_cluster_items": _delete_older_than(db, "trend_cluster_items", cutoff),
            "trend_patterns": _delete_older_than(db, "trend_patterns", cutoff),
            "analytics_events": _delete_older_than(db, "analytics_events", cutoff),
            "generated_posts": _delete_older_than(db, "generated_posts", cutoff),
        }

    capped = {
        "scraped_posts": _cap_collection(db, "scraped_posts", _max_scraped_posts()),
        "generated_posts": _cap_collection(db, "generated_posts", _max_generated_posts()),
        "trend_clusters": _cap_collection(db, "trend_clusters", _max_trend_clusters()),
        "analytics_events": _cap_collection(db, "analytics_events", _max_analytics_events()),
    }

    return {"deleted": deleted, "capped": capped}
