from fastapi import APIRouter
from fastapi import Query, HTTPException

from app.services.generator_service import (
    generate_tweet
)

router = APIRouter()


@router.get("/generate/{topic}")
def generate(topic: str, count: int = Query(1, ge=1, le=11)):

    total = max(1, min(11, count))
    items = []

    warning = None

    for _ in range(total):
        try:
            post = generate_tweet(topic)
        except HTTPException as exc:
            # If upstream model quota is exhausted or returns a rate-limit,
            # stop generating more items and return what we have so far
            if exc.status_code == 429:
                warning = exc.detail if hasattr(exc, "detail") else "Rate limited by generation backend"
                break
            # Propagate other HTTP errors
            raise

        items.append({
            "id": str(post.get("_id")),
            "topic": post.get("topic"),
            "generated_tweet": post.get("generated_text"),
        })

    result = {
        "count": len(items),
        "items": items,
    }

    if warning:
        result["warning"] = warning

    return result