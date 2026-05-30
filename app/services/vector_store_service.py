from __future__ import annotations

import logging
import os
from typing import Iterable

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qdrant_models
except ModuleNotFoundError:  # pragma: no cover - environment-specific fallback
    QdrantClient = None
    qdrant_models = None


_COLLECTION = os.getenv("QDRANT_COLLECTION", "scraped_posts")
logger = logging.getLogger("uvicorn.error")


def qdrant_health() -> dict:
    if QdrantClient is None:
        return {
            "available": False,
            "configured": False,
            "reason": "qdrant-client is not installed",
        }

    try:
        client = _get_client()
        client.get_collections()
        return {
            "available": True,
            "configured": True,
            "reason": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "configured": True,
            "reason": str(exc),
        }


def _get_client() -> QdrantClient:
    if QdrantClient is None:
        raise RuntimeError(
            "qdrant-client is not installed. Install requirements.txt to enable vector store features."
        )
    url = os.getenv("QDRANT_URL", "http://localhost:6333").strip()
    if not url:
        raise RuntimeError("QDRANT_URL is not configured")
    api_key = os.getenv("QDRANT_API_KEY") or None
    return QdrantClient(url=url, api_key=api_key)


def ensure_collection(vector_size: int) -> None:
    client = _get_client()

    try:
        if qdrant_models is None:
            raise RuntimeError(
                "qdrant-client is not installed. Install requirements.txt to enable vector store features."
            )
        info = client.get_collection(_COLLECTION)
        existing_size = info.config.params.vectors.size
        if existing_size != vector_size:
            raise RuntimeError(
                f"Qdrant collection size mismatch: {existing_size} != {vector_size}."
            )
        return
    except Exception as exc:
        logger.warning("Qdrant collection check failed: %s", exc)

    try:
        if qdrant_models is None:
            raise RuntimeError(
                "qdrant-client is not installed. Install requirements.txt to enable vector store features."
            )
        client.recreate_collection(
            collection_name=_COLLECTION,
            vectors_config=qdrant_models.VectorParams(
                size=vector_size,
                distance=qdrant_models.Distance.COSINE,
            ),
        )
    except Exception as exc:
        raise RuntimeError(f"Qdrant collection create failed: {exc}") from exc


def upsert_embeddings(items: Iterable[dict]) -> None:
    items = list(items)
    if not items:
        return

    vectors = [item["vector"] for item in items]
    vector_size = len(vectors[0])
    try:
        ensure_collection(vector_size)
    except Exception as exc:
        raise RuntimeError(f"Qdrant ensure collection failed: {exc}") from exc

    points = []
    for item in items:
        if qdrant_models is None:
            raise RuntimeError(
                "qdrant-client is not installed. Install requirements.txt to enable vector store features."
            )
        point = qdrant_models.PointStruct(
            id=item["vector_id"],
            vector=item["vector"],
            payload=item.get("payload", {}),
        )
        points.append(point)

    client = _get_client()
    try:
        client.upsert(collection_name=_COLLECTION, points=points)
    except Exception as exc:
        raise RuntimeError(f"Qdrant upsert failed: {exc}") from exc


def retrieve_vector(vector_id: str):
    client = _get_client()
    try:
        result = client.retrieve(
            collection_name=_COLLECTION,
            ids=[vector_id],
            with_vectors=True,
            with_payload=True,
        )
        return result[0] if result else None
    except Exception as exc:
        raise RuntimeError(f"Qdrant retrieve failed: {exc}") from exc


def search_vectors(query_vector: list[float], top_k: int, filters: dict | None = None):
    client = _get_client()
    if qdrant_models is None:
        raise RuntimeError(
            "qdrant-client is not installed. Install requirements.txt to enable vector store features."
        )

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

    try:
        return client.search(
            collection_name=_COLLECTION,
            query_vector=query_vector,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:
        raise RuntimeError(f"Qdrant search failed: {exc}") from exc


def scroll_vectors(filters: dict | None = None, limit: int = 1000):
    client = _get_client()
    if qdrant_models is None:
        raise RuntimeError(
            "qdrant-client is not installed. Install requirements.txt to enable vector store features."
        )

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
        try:
            points, next_offset = client.scroll(
                collection_name=_COLLECTION,
                scroll_filter=query_filter,
                with_vectors=True,
                with_payload=True,
                limit=limit,
                offset=next_offset,
            )
        except Exception as exc:
            raise RuntimeError(f"Qdrant scroll failed: {exc}") from exc
        results.extend(points)
        if next_offset is None:
            break

    return results
