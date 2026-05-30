from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from math import sqrt

import numpy as np
from sklearn.cluster import MiniBatchKMeans
from app.services.mongo_utils import to_object_id
from app.services.pattern_service import build_patterns_for_cluster
from app.services.vector_store_service import scroll_vectors


def _calculate_engagement(post: dict) -> float:
    return float((post.get("likes") or 0) + (post.get("reposts") or 0) + (post.get("replies") or 0))


def run_trend_pipeline(
    db,
    window_hours: int = 6,
    min_posts: int = 25,
) -> dict:
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=window_hours)

    points = scroll_vectors(filters={"since_ts": window_start.timestamp()})
    if len(points) < min_posts:
        return {"clusters": 0, "reason": "not_enough_posts"}

    vectors = np.array([point.vector for point in points])

    k = int(sqrt(len(points)))
    k = max(2, min(8, k))

    kmeans = MiniBatchKMeans(n_clusters=k, random_state=42)
    labels = kmeans.fit_predict(vectors)

    clusters_created = 0

    for cluster_index in range(k):
        cluster_points = [p for p, label in zip(points, labels) if label == cluster_index]
        if not cluster_points:
            continue

        post_ids = []
        for point in cluster_points:
            payload = point.payload or {}
            post_id = payload.get("scraped_post_id")
            oid = to_object_id(post_id)
            if oid:
                post_ids.append(oid)

        posts = list(db.scraped_posts.find({"_id": {"$in": post_ids}}))
        if not posts:
            continue

        avg_likes = np.mean([post.get("likes") or 0 for post in posts])
        avg_reposts = np.mean([post.get("reposts") or 0 for post in posts])
        avg_replies = np.mean([post.get("replies") or 0 for post in posts])

        velocity_score = (avg_likes + avg_reposts + avg_replies) / max(1, window_hours)

        cluster_doc = {
            "topic": posts[0].get("topic") if isinstance(posts[0], dict) else getattr(posts[0], "topic", None),
            "window_start": window_start,
            "window_end": window_end,
            "size": len(posts),
            "avg_likes": float(avg_likes),
            "avg_reposts": float(avg_reposts),
            "avg_replies": float(avg_replies),
            "velocity_score": float(velocity_score),
            "label": None,
            "centroid_json": json.dumps(kmeans.cluster_centers_[cluster_index].tolist()),
            "created_at": datetime.now(timezone.utc),
        }

        result = db.trend_clusters.insert_one(cluster_doc)
        cluster_id = result.inserted_id

        ranked = sorted(posts, key=_calculate_engagement, reverse=True)
        for rank, post in enumerate(ranked[:100], start=1):
            db.trend_cluster_items.insert_one({
                "cluster_id": cluster_id,
                "scraped_post_id": post.get("_id"),
                "rank": rank,
                "engagement_score": _calculate_engagement(post),
                "created_at": datetime.now(timezone.utc),
            })

        build_patterns_for_cluster(db, str(cluster_id))
        clusters_created += 1

    return {"clusters": clusters_created, "window_hours": window_hours}
