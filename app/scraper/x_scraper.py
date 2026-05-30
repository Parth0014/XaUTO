import os
import hashlib
from datetime import datetime, timezone

import requests
from requests_oauthlib import OAuth1

from app.database import get_db_client
from app.services.scrape_progress import update_scrape_progress
from app.services.text_cleaner import (
    sanitize_reference_text,
    normalize_content,
    detect_language_simple,
)
from app.services.nlp_processor import detect_topic, analyze_sentiments
from app.services.embedding_pipeline import embed_and_store_posts

DEFAULT_TOPICS = [
    "programming",
    "ai",
    "devtools",
    "webdev",
    "javascript",
    "python",
    "startup",
]


def sanitize_content(text: str) -> str:
    return sanitize_reference_text(text)


def _get_oauth() -> OAuth1:
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("X API credentials are not configured")

    return OAuth1(api_key, api_secret, access_token, access_secret)


def _build_query() -> str:
    explicit = os.getenv("X_SEARCH_QUERY", "").strip()
    if explicit:
        return explicit

    topics_env = os.getenv("X_SEARCH_TOPICS", "").strip()
    topics = [
        topic.strip()
        for topic in topics_env.split(",")
        if topic.strip()
    ]
    if not topics:
        topics = list(DEFAULT_TOPICS)

    formatted = []
    for topic in topics:
        if " " in topic:
            formatted.append(f'"{topic}"')
        else:
            formatted.append(topic)

    topic_query = " OR ".join(formatted)
    return f"({topic_query}) lang:en -is:retweet -is:reply"


def _fetch_recent_tweets(max_results: int) -> dict:
    url = "https://api.x.com/2/tweets/search/recent"
    params = {
        "query": _build_query(),
        "max_results": max_results,
        "tweet.fields": "created_at,lang,public_metrics,author_id",
        "expansions": "author_id",
        "user.fields": "name,username",
    }

    response = requests.get(url, params=params, auth=_get_oauth(), timeout=30)
    if not response.ok:
        raise RuntimeError(
            f"X API error {response.status_code}: {response.text}"
        )

    return response.json()


def scrape_x_trends():
    db = get_db_client()
    seen_hashes = set()
    error_count = 0

    update_scrape_progress(
        state="running",
        message="Fetching posts from X API recent search.",
        chrome="not_required",
        last_error=None,
    )

    max_results = int(os.getenv("X_SEARCH_MAX_RESULTS", "25"))
    max_results = max(10, min(100, max_results))

    try:
        payload = _fetch_recent_tweets(max_results)
    except Exception as error:
        update_scrape_progress(
            state="error",
            message="Failed to fetch X API results.",
            last_error=str(error),
        )
        raise

    tweets = payload.get("data", [])
    users = {
        user["id"]: user
        for user in payload.get("includes", {}).get("users", [])
    }

    update_scrape_progress(
        cycle=1,
        seen=len(tweets),
        message=f"Fetched {len(tweets)} tweets from X API.",
    )

    scraped_candidates = []

    for tweet in tweets:
        try:
            text = sanitize_content(tweet.get("text", ""))
            if not text:
                continue

            normalized = normalize_content(text)
            content_hash = hashlib.sha256(
                normalized.encode("utf-8")
            ).hexdigest()

            tweet_id = tweet.get("id")
            if content_hash in seen_hashes:
                continue

            if tweet_id:
                existing = db.scraped_posts.find_one({
                    "$or": [
                        {"tweet_id": tweet_id},
                        {"content_hash": content_hash},
                        {"content": text},
                    ]
                })
            else:
                existing = db.scraped_posts.find_one({
                    "$or": [
                        {"content_hash": content_hash},
                        {"content": text},
                    ]
                })

            if existing:
                seen_hashes.add(content_hash)
                continue

            metrics = tweet.get("public_metrics") or {}
            author = users.get(tweet.get("author_id", ""), {})
            username = author.get("name") or "Unknown"
            handle = author.get("username")

            topic = detect_topic(text)

            scraped_candidates.append({
                "tweet_id": tweet_id,
                "content_hash": content_hash,
                "username": username,
                "handle": handle,
                "tweet_content": text,
                "normalized_content": normalized,
                "language": tweet.get("lang") or detect_language_simple(text),
                "replies": int(metrics.get("reply_count", 0)),
                "reposts": int(metrics.get("retweet_count", 0)),
                "likes": int(metrics.get("like_count", 0)),
                "views": int(metrics.get("impression_count", 0)),
                "topic": topic,
            })

            update_scrape_progress(
                last_author=username,
                last_topic=topic,
                last_content=text[:180],
                message=f"Captured {username} in topic {topic or 'unknown'}.",
            )
        except Exception as error:
            error_count += 1
            update_scrape_progress(
                last_error=str(error),
                message="Failed to process an X API tweet, continuing.",
            )
            continue

    sentiments = analyze_sentiments(
        [candidate["tweet_content"] for candidate in scraped_candidates]
    )

    new_posts = []
    for candidate, sentiment in zip(scraped_candidates, sentiments):
        post = {
            "platform": "x",
            "tweet_id": candidate["tweet_id"],
            "author": candidate["username"],
            "handle": candidate["handle"],
            "content": candidate["tweet_content"],
            "normalized_content": candidate["normalized_content"],
            "content_hash": candidate["content_hash"],
            "language": candidate["language"],
            "likes": candidate["likes"],
            "replies": candidate["replies"],
            "reposts": candidate["reposts"],
            "views": candidate["views"],
            "topic": candidate["topic"],
            "sentiment": sentiment,
            "created_at": datetime.now(timezone.utc),
        }

        new_posts.append(post)
        seen_hashes.add(candidate["content_hash"])

    inserted_count = 0
    if new_posts:
        result = db.scraped_posts.insert_many(new_posts)
        for post, oid in zip(new_posts, result.inserted_ids):
            post["_id"] = oid
        inserted_count = len(result.inserted_ids)

    if new_posts:
        try:
            embed_and_store_posts(db, new_posts)
        except Exception as error:
            update_scrape_progress(
                last_error=str(error),
                message="Embedding failed. Check Qdrant or embedding config.",
            )
            print("EMBEDDING ERROR:", error)
            error_count += 1

    update_scrape_progress(
        state="idle",
        message=(
            f"X API scrape complete with {inserted_count} new posts"
            f" and {error_count} errors."
        ),
        inserted=inserted_count,
    )