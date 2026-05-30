"""
routes/maintenance.py  —  updated with relabel-topics endpoint
"""
from fastapi import APIRouter, Depends

from app.database import get_db
from app.services.text_cleaner import sanitize_reference_text
from app.services.nlp_processor import detect_topic

router = APIRouter()


@router.post("/maintenance/clean-scraped-posts")
def clean_scraped_posts(
    apply: bool = False,
    relabel_topics: bool = True,
    limit: int = 500,
    db=Depends(get_db),
):
    posts = list(
        db.scraped_posts
        .find()
        .sort("_id", -1)
        .limit(limit)
    )

    updated = 0
    samples = []

    for post in posts:
        original = post.get("content") or ""
        cleaned = sanitize_reference_text(original)
        if cleaned != original:
            if apply:
                db.scraped_posts.update_one(
                    {"_id": post.get("_id")},
                    {"$set": {"content": cleaned}}
                )
                if relabel_topics:
                    db.scraped_posts.update_one(
                        {"_id": post.get("_id")},
                        {"$set": {"topic": detect_topic(cleaned)}}
                    )
            updated += 1
            if len(samples) < 5:
                samples.append({
                    "id": str(post.get("_id")),
                    "before": original[:140],
                    "after": cleaned[:140],
                })

    return {
        "checked": len(posts),
        "updated": updated,
        "applied": apply,
        "samples": samples,
    }


@router.post("/maintenance/relabel-topics")
def relabel_topics(
    apply: bool = False,
    limit: int = 2000,
    db=Depends(get_db),
):
    """
    Re-run topic detection on all scraped posts using the improved
    nlp_processor.detect_topic().  Use apply=true to actually write changes.
    """
    posts = list(
        db.scraped_posts
        .find()
        .sort("_id", -1)
        .limit(limit)
    )

    changes = 0
    topic_counts: dict[str, int] = {}
    samples = []

    for post in posts:
        content = sanitize_reference_text(post.get("content") or "")
        new_topic = detect_topic(content)
        old_topic = post.get("topic") or "general"

        topic_counts[new_topic] = topic_counts.get(new_topic, 0) + 1

        if new_topic != old_topic:
            changes += 1
            if apply:
                db.scraped_posts.update_one(
                    {"_id": post.get("_id")},
                    {"$set": {"topic": new_topic}}
                )
            if len(samples) < 10:
                samples.append({
                    "id": str(post.get("_id")),
                    "old_topic": old_topic,
                    "new_topic": new_topic,
                    "content": content[:100],
                })

    return {
        "checked": len(posts),
        "changed": changes,
        "applied": apply,
        "new_distribution": topic_counts,
        "samples": samples,
    }