from __future__ import annotations

import json
import re
from datetime import datetime
from statistics import mean, median

from app.services.mongo_utils import to_object_id


def _extract_hooks(texts: list[str], max_hooks: int = 10) -> list[str]:
    hooks = []
    for text in texts:
        parts = text.split()
        if len(parts) >= 4:
            hooks.append(" ".join(parts[:4]))
    hooks = [h for h in hooks if h]
    return hooks[:max_hooks]


def _pattern_from_texts(texts: list[str]) -> dict:
    lengths = [len(text) for text in texts if text]
    if not lengths:
        return {}

    sentence_counts = [max(1, len(re.split(r"[.!?]", text)) - 1) for text in texts]
    question_rate = sum(1 for text in texts if "?" in text) / len(texts)

    punctuation = {
        "exclamation": sum(text.count("!") for text in texts),
        "question": sum(text.count("?") for text in texts),
        "ellipses": sum(text.count("...") for text in texts),
    }

    return {
        "length_avg": mean(lengths),
        "length_median": median(lengths),
        "sentences_avg": mean(sentence_counts),
        "question_rate": question_rate,
        "punctuation": punctuation,
        "hooks": _extract_hooks(texts),
    }


def build_patterns_for_cluster(db, cluster_id: str, top_n: int = 20) -> dict | None:
    oid = to_object_id(cluster_id)
    if not oid:
        return None

    items = list(
        db.trend_cluster_items
        .find({"cluster_id": oid})
        .sort("engagement_score", -1)
        .limit(top_n)
    )

    if not items:
        return None

    post_ids = [item.get("scraped_post_id") for item in items if item.get("scraped_post_id")]
    posts = list(db.scraped_posts.find({"_id": {"$in": post_ids}}))

    texts = [post.content for post in posts if post.content]
    if not texts:
        return None

    pattern = _pattern_from_texts(texts)
    summary = (
        f"Avg length {pattern.get('length_avg', 0):.1f}, "
        f"questions {pattern.get('question_rate', 0) * 100:.1f}%"
    )

    record = {
        "cluster_id": oid,
        "summary": summary,
        "pattern_json": json.dumps(pattern),
        "created_at": datetime.utcnow(),
    }

    db.trend_patterns.insert_one(record)

    return record
