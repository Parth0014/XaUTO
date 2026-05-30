from __future__ import annotations

import os
from typing import Iterable

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models


_COLLECTION = os.getenv("QDRANT_COLLECTION", "scraped_posts")


def _get_client() -> QdrantClient:
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    api_key = os.getenv("QDRANT_API_KEY")
    return QdrantClient(url=url, api_key=api_key)


def ensure_collection(vector_size: int) -> None:
    client = _get_client()

    try:
        info = client.get_collection(_COLLECTION)
        existing_size = info.config.params.vectors.size
        if existing_size != vector_size:
            raise RuntimeError(
                f"Qdrant collection size mismatch: {existing_size} != {vector_size}."
            )
        return
    except Exception:
        pass

    client.recreate_collection(
        collection_name=_COLLECTION,
        vectors_config=qdrant_models.VectorParams(
            size=vector_size,
            distance=qdrant_models.Distance.COSINE,
        ),
    )


def upsert_embeddings(items: Iterable[dict]) -> None:
    items = list(items)
    if not items:
        return

    vectors = [item["vector"] for item in items]
    vector_size = len(vectors[0])
    ensure_collection(vector_size)

    points = []
    for item in items:
        point = qdrant_models.PointStruct(
            id=item["vector_id"],
            vector=item["vector"],
            payload=item.get("payload", {}),
        )
        points.append(point)

    client = _get_client()
    client.upsert(collection_name=_COLLECTION, points=points)


def retrieve_vector(vector_id: str):
    client = _get_client()
    result = client.retrieve(
        collection_name=_COLLECTION,
        ids=[vector_id],
        with_vectors=True,
        with_payload=True,
    )
    return result[0] if result else None


def search_vectors(query_vector: list[float], top_k: int, filters: dict | None = None):
    client = _get_client()

    must_conditions = []

    if filters:
        topic = filters.get("topic")
        if topic:
            must_conditions.append(
                qdrant_models.FieldCondition(
                    key="topic",
                    match=qdrant_models.MatchValue(value=topic),
                )
            )

        language = filters.get("language")
        if language:
            must_conditions.append(
                qdrant_models.FieldCondition(
                    key="language",
                    match=qdrant_models.MatchValue(value=language),
                )
            )

        min_likes = filters.get("min_likes")
        if min_likes is not None:
            must_conditions.append(
                qdrant_models.FieldCondition(
                    key="likes",
                    range=qdrant_models.Range(gte=float(min_likes)),
                )
            )

        since_ts = filters.get("since_ts")
        if since_ts is not None:
            must_conditions.append(
                qdrant_models.FieldCondition(
                    key="created_at_ts",
                    range=qdrant_models.Range(gte=float(since_ts)),
                )
            )

    query_filter = qdrant_models.Filter(must=must_conditions) if must_conditions else None

    return client.search(
        collection_name=_COLLECTION,
        query_vector=query_vector,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
        with_vectors=False,
    )


def scroll_vectors(filters: dict | None = None, limit: int = 1000):
    client = _get_client()

    must_conditions = []

    if filters:
        since_ts = filters.get("since_ts")
        if since_ts is not None:
            must_conditions.append(
                qdrant_models.FieldCondition(
                    key="created_at_ts",
                    range=qdrant_models.Range(gte=float(since_ts)),
                )
            )

    query_filter = qdrant_models.Filter(must=must_conditions) if must_conditions else None

    results = []
    next_offset = None

    while True:
        points, next_offset = client.scroll(
            collection_name=_COLLECTION,
            scroll_filter=query_filter,
            with_vectors=True,
            with_payload=True,
            limit=limit,
            offset=next_offset,
        )
        results.extend(points)
        if next_offset is None:
            break

    return results
