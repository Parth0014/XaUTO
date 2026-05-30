from __future__ import annotations

import os
from datetime import datetime, timezone
import logging
from typing import Any

import requests
from requests_oauthlib import OAuth1
from app.services.event_broadcaster import publish_sync, make_event

logger = logging.getLogger("uvicorn.error")


def _get_oauth() -> OAuth1:
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("X API credentials are not configured")

    return OAuth1(api_key, api_secret, access_token, access_secret)


def fetch_post_metrics(db, limit: int = 100) -> int:
    """Fetch public metrics for recently posted generated posts and update DB.

    Returns the number of posts updated.
    """
    q = {
        "posted": True,
        "post_external_id": {"$ne": ""},
    }
    # exclude dry-run posts
    cursor = db.generated_posts.find(q).limit(limit)

    updated = 0
    for doc in cursor:
        external = doc.get("post_external_id")
        if not external or external == "dry_run":
            continue

        tweet_id = str(external)
        url = f"https://api.x.com/2/tweets/{tweet_id}"
        params = {"tweet.fields": "public_metrics"}
        try:
            resp = requests.get(url, params=params, auth=_get_oauth(), timeout=15)
            if not resp.ok:
                logger.warning("Failed to fetch tweet %s: %s", tweet_id, resp.text[:200])
                continue
            data = resp.json()
            metrics = data.get("data", {}).get("public_metrics", {})
            likes = int(metrics.get("like_count", 0))
            reposts = int(metrics.get("retweet_count", 0))
            replies = int(metrics.get("reply_count", 0))
            # impressions not always available
            impressions = int(metrics.get("impression_count", 0)) if metrics.get("impression_count") is not None else None

            update = {
                "actual_likes": likes,
                "actual_reposts": reposts,
                "actual_replies": replies,
                "last_metrics_updated_at": datetime.now(timezone.utc),
            }
            if impressions is not None:
                update["actual_views"] = impressions

            db.generated_posts.update_one({"_id": doc.get("_id")}, {"$set": update})
            updated += 1
        except Exception as exc:
            logger.warning("Error fetching metrics for tweet %s: %s", tweet_id, exc)
            continue

    # publish a summary event for frontend
    try:
        publish_sync(make_event("post_feedback", {"updated": updated}))
    except Exception:
        pass

    return updated
