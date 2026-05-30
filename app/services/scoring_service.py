from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.text_cleaner import sanitize_reference_text
from app.services.reward_service import combined_reward_score
from app.services.groq_client import call_groq
from app.services.event_broadcaster import publish_sync, make_event

_URL_RE = re.compile(r"https?://\S+|\bx\.com/\S+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#\w+")
_PROMPTY_RE = re.compile(
    r"\b(we need|must be|under 280|no hashtags|no emojis|double-check|ask rhetoric)\b",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF]"
)


def _trend_boost(db, topic: str | None) -> float:
    if not topic:
        return 0.0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
    cluster = db.trend_clusters.find_one(
        {"topic": topic, "window_end": {"$gte": cutoff}},
        sort=[("window_end", -1)],
    )

    if not cluster:
        return 0.0

    velocity = float(cluster.get("velocity_score") or 0)
    size = float(cluster.get("size") or 0)

    velocity_boost = math.log10(1 + max(0.0, velocity)) * 3.0
    size_boost = min(4.0, size / 50.0)

    return min(10.0, velocity_boost + size_boost)


def _length_score(length: int) -> float:
    if length < 60:
        return -min(15.0, (60 - length) / 3.0)
    if length > 260:
        return -min(15.0, (length - 260) / 4.0)
    return min(12.0, (length - 60) / 15.0)


def _uniqueness_score(tokens: list[str]) -> float:
    if not tokens:
        return -10.0

    unique_ratio = len(set(tokens)) / len(tokens)
    if unique_ratio < 0.4:
        return -8.0
    if unique_ratio > 0.65:
        return 5.0
    return 0.0


def _structure_score(text: str) -> float:
    sentences = len(re.findall(r"[.!?]", text))
    score = 0.0

    if sentences >= 2:
        score += 4.0
    elif sentences == 0:
        score -= 4.0

    if "?" in text:
        score += 3.0

    return score


def _penalty_score(raw_text: str, cleaned: str) -> float:
    penalty = 0.0

    if _URL_RE.search(raw_text):
        penalty -= 15.0
    if _HASHTAG_RE.search(raw_text):
        penalty -= 10.0
    if _PROMPTY_RE.search(raw_text):
        penalty -= 12.0
    if _EMOJI_RE.search(raw_text):
        penalty -= 5.0

    if len(cleaned) < 25:
        penalty -= 20.0

    return penalty


def score_text(text: str, trend_boost: float = 0.0) -> dict[str, Any]:
    raw = text or ""
    cleaned = sanitize_reference_text(raw)
    length = len(cleaned)
    tokens = re.findall(r"[A-Za-z0-9']+", cleaned.lower())

    base = 50.0
    score = base

    score += _length_score(length)
    score += _structure_score(cleaned)
    score += _uniqueness_score(tokens)
    score += _penalty_score(raw, cleaned)
    score += trend_boost

    score = max(0.0, min(100.0, score))

    return {
        "cleaned_length": length,
        "token_count": len(tokens),
        "trend_boost": round(trend_boost, 2),
        "score": round(score, 2),
    }


def score_generated_post(db, post: dict) -> dict[str, Any]:
    trend_boost = _trend_boost(db, post.get("topic"))
    breakdown = score_text(post.get("generated_text") or "", trend_boost=trend_boost)

    # Compute reward-model score (semantic + optional groq second-opinion)
    try:
        reward_score = combined_reward_score(db, post.get("generated_text") or "", groq_callable=call_groq)
    except Exception:
        reward_score = 0.0

    # Combine heuristic breakdown score and reward model: give reward model higher weight
    base_score = float(breakdown.get("score", 0.0))
    combined = round(max(0.0, min(100.0, (0.4 * base_score) + (0.6 * reward_score))), 2)

    db.generated_posts.update_one(
        {"_id": post.get("_id")},
        {"$set": {"predicted_score": float(combined), "reward_score": float(reward_score)}}
    )

    try:
        publish_sync(make_event("scoring", {"post_id": str(post.get("_id")), "predicted_score": float(combined), "reward_score": float(reward_score)}))
    except Exception:
        pass

    breakdown["post_id"] = str(post.get("_id"))
    breakdown["reward_score"] = float(reward_score)
    breakdown["combined_score"] = float(combined)
    return breakdown


def score_unscored_posts(db, limit: int = 50) -> dict[str, Any]:
    posts = list(
        db.generated_posts
        .find({
            "status": "generated",
            "$or": [
                {"predicted_score": {"$exists": False}},
                {"predicted_score": {"$lte": 0}},
            ],
        })
        .sort("created_at", -1)
        .limit(limit)
    )

    scored = []
    for post in posts:
        scored.append(score_generated_post(db, post))

    avg_score = 0.0
    if scored:
        avg_score = sum(item["score"] for item in scored) / len(scored)

    return {
        "scored": len(scored),
        "avg_score": round(avg_score, 2),
        "items": scored,
    }
