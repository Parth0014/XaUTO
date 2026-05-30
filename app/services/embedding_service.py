from __future__ import annotations

import os
from threading import Lock
from typing import Iterable

import httpx


_EMBEDDER = None
_EMBEDDER_LOCK = Lock()


def _get_provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()


def _get_local_model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


def _load_local_embedder():
    global _EMBEDDER

    if _EMBEDDER is not None:
        return _EMBEDDER

    with _EMBEDDER_LOCK:
        if _EMBEDDER is not None:
            return _EMBEDDER

        from sentence_transformers import SentenceTransformer

        model_name = _get_local_model_name()
        _EMBEDDER = SentenceTransformer(model_name)
        return _EMBEDDER


def _embed_local(texts: list[str]) -> list[list[float]]:
    embedder = _load_local_embedder()
    vectors = embedder.encode(texts, normalize_embeddings=True)
    return [vec.tolist() for vec in vectors]


def _embed_groq(texts: list[str]) -> list[list[float]]:
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("Groq_API_KEY")
    embeddings_url = os.getenv("GROQ_EMBEDDINGS_URL") or os.getenv("GROQ_API_URL")
    model = os.getenv("GROQ_EMBEDDINGS_MODEL", "nomic-embed-text-v1.5")

    if not api_key or not embeddings_url:
        raise RuntimeError("Groq embeddings not configured. Set GROQ_API_KEY and GROQ_EMBEDDINGS_URL.")

    base = embeddings_url.rstrip("/")
    if base.endswith("/embeddings"):
        endpoint = base
    else:
        endpoint = base + "/embeddings"

    payload = {
        "model": model,
        "input": texts,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, dict) and "data" in data:
        vectors = []
        for item in data["data"]:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not vector:
                continue
            vectors.append(vector)
        if vectors:
            return vectors

    raise RuntimeError("Unexpected embeddings response format.")


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    cleaned = [t.strip() for t in texts if t and t.strip()]
    if not cleaned:
        return []

    provider = _get_provider()
    if provider == "groq":
        return _embed_groq(cleaned)

    return _embed_local(cleaned)
