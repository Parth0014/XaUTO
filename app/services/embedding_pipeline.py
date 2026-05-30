from __future__ import annotations

import hashlib
import logging
from typing import Iterable

from app.services.embedding_service import embed_texts
from app.services.text_cleaner import normalize_content, detect_language_simple
from app.services.vector_store_service import upsert_embeddings

logger = logging.getLogger("uvicorn.error")


def _build_payload(post: dict) -> dict:
    created_at = post.get("created_at")
    created_at_ts = created_at.timestamp() if created_at else None

    return {
        "scraped_post_id": str(post.get("_id")),
        "topic": post.get("topic"),
        "language": post.get("language"),
        "likes": post.get("likes") or 0,
        "replies": post.get("replies") or 0,
        "reposts": post.get("reposts") or 0,
        "views": post.get("views") or 0,
        "created_at_ts": created_at_ts,
    }


def embed_and_store_posts(db, posts: Iterable[dict]) -> int:
    posts = [post for post in posts if post]
    if not posts:
        return 0

    for post in posts:
        normalized_content = post.get("normalized_content")
        if not normalized_content:
            normalized_content = normalize_content(post.get("content") or "")
            post["normalized_content"] = normalized_content

        content_hash = post.get("content_hash")
        if not content_hash:
            normalized = normalized_content or normalize_content(post.get("content") or "")
            content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            post["content_hash"] = content_hash

        if not post.get("language"):
            post["language"] = detect_language_simple(post.get("content") or "")

    posts = [post for post in posts if post.get("normalized_content")]
    if not posts:
        return 0

    post_ids = [post.get("_id") for post in posts if post.get("_id")]
    existing_ids = {
        rec.get("scraped_post_id")
        for rec in db.embedding_records.find({"scraped_post_id": {"$in": post_ids}}, {"scraped_post_id": 1})
    }

    new_posts = [post for post in posts if post.get("_id") not in existing_ids]
    if not new_posts:
        return 0

    texts = [post.get("normalized_content") for post in new_posts]
    try:
        vectors = embed_texts(texts)
    except Exception as exc:
        logger.warning("Embedding generation failed: %s", exc)
        raise RuntimeError(f"Embedding generation failed: {exc}") from exc

    items = []
    for post, vector in zip(new_posts, vectors):
        items.append({
            "vector_id": post.get("content_hash"),
            "vector": vector,
            "payload": _build_payload(post),
        })

    try:
        upsert_embeddings(items)
    except Exception as exc:
        logger.warning("Qdrant upsert failed: %s", exc)
        raise

    inserted = 0
    for post, vector in zip(new_posts, vectors):
        try:
            db.embedding_records.insert_one({
                "scraped_post_id": post.get("_id"),
                "vector_id": post.get("content_hash"),
                "model": "default",
                "dims": len(vector),
                "created_at": post.get("created_at"),
            })

            db.scraped_posts.update_one(
                {"_id": post.get("_id")},
                {"$set": {
                    "normalized_content": post.get("normalized_content"),
                    "content_hash": post.get("content_hash"),
                    "language": post.get("language"),
                }}
            )
            inserted += 1
        except Exception as exc:
            logger.warning("Embedding record write failed: %s", exc)

    return inserted


def backfill_embeddings(db, limit: int = 200) -> int:
    candidates = list(
        db.scraped_posts
        .find({"normalized_content": {"$ne": None}})
        .sort("_id", -1)
        .limit(limit * 2)
    )

    if not candidates:
        return 0

    candidate_ids = [post.get("_id") for post in candidates if post.get("_id")]
    existing_ids = {
        rec.get("scraped_post_id")
        for rec in db.embedding_records.find({"scraped_post_id": {"$in": candidate_ids}}, {"scraped_post_id": 1})
    }

    candidates = [post for post in candidates if post.get("_id") not in existing_ids]

    try:
        return embed_and_store_posts(db, candidates)
    except Exception as exc:
        logger.warning("Embedding backfill failed: %s", exc)
        return 0
