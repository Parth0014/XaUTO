from __future__ import annotations

from bson import ObjectId

from app.services.embedding_service import embed_texts
from app.services.mongo_utils import to_object_id
from app.services.vector_store_service import search_vectors, retrieve_vector


def retrieve_similar_posts(
    db,
    query_text: str,
    top_k: int = 10,
    topic: str | None = None,
    min_likes: int | None = None,
    since_ts: float | None = None,
    language: str | None = None,
) -> list[dict]:
    vectors = embed_texts([query_text])
    if not vectors:
        return []

    filters = {
        "topic": topic,
        "min_likes": min_likes,
        "since_ts": since_ts,
        "language": language,
    }

    results = search_vectors(vectors[0], top_k, filters)
    ids: list[ObjectId] = []
    for hit in results:
        payload = hit.payload or {}
        post_id = payload.get("scraped_post_id")
        if post_id is not None:
            parsed = to_object_id(post_id)
            if parsed:
                ids.append(parsed)

    if not ids:
        return []

    posts = list(db.scraped_posts.find({"_id": {"$in": ids}}))
    lookup = {post["_id"]: post for post in posts}
    ordered = [lookup.get(post_id) for post_id in ids]
    return [post for post in ordered if post is not None]


def retrieve_similar_by_post_id(
    db,
    post_id: str,
    top_k: int = 10,
    topic: str | None = None,
    min_likes: int | None = None,
    since_ts: float | None = None,
    language: str | None = None,
) -> list[dict]:
    oid = to_object_id(post_id)
    if not oid:
        return []

    post = db.scraped_posts.find_one({"_id": oid})
    if not post or not post.get("content_hash"):
        return []

    vector_point = retrieve_vector(post.get("content_hash"))
    if not vector_point or not vector_point.vector:
        return []

    filters = {
        "topic": topic,
        "min_likes": min_likes,
        "since_ts": since_ts,
        "language": language,
    }

    results = search_vectors(vector_point.vector, top_k + 1, filters)

    ids: list[ObjectId] = []
    for hit in results:
        payload = hit.payload or {}
        hit_id = payload.get("scraped_post_id")
        if hit_id is not None and str(hit_id) != str(post_id):
            parsed = to_object_id(hit_id)
            if parsed:
                ids.append(parsed)

    if not ids:
        return []

    posts = list(db.scraped_posts.find({"_id": {"$in": ids}}))
    lookup = {post["_id"]: post for post in posts}
    ordered = [lookup.get(pid) for pid in ids]
    return [item for item in ordered if item is not None]
