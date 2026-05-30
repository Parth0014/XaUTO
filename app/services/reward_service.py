from __future__ import annotations

import logging
import math
from typing import Iterable

from app.services.embedding_service import embed_texts
from app.services.vector_store_service import search_vectors
from app.services.text_cleaner import sanitize_reference_text

logger = logging.getLogger("uvicorn.error")


def _cosine(a: Iterable[float], b: Iterable[float]) -> float:
    try:
        da = sum(x * x for x in a) ** 0.5
        db = sum(x * x for x in b) ** 0.5
        if da == 0 or db == 0:
            return 0.0
        return sum(x * y for x, y in zip(a, b)) / (da * db)
    except Exception:
        return 0.0


def semantic_similarity_to_top_posts(db, text: str, top_k: int = 10) -> float:
    """Return the max cosine similarity between `text` and high-performing posts.

    Uses the vector store retrieval API to get nearest vectors for `text` and
    computes the highest cosine similarity to those vectors' payloads.
    """
    if not text:
        return 0.0

    cleaned = sanitize_reference_text(text)
    if not cleaned:
        return 0.0

    try:
        vector = embed_texts([cleaned])[0]
    except Exception as exc:
        logger.warning("Embedding for reward model failed: %s", exc)
        return 0.0

    # Attempt to retrieve nearest vectors from vector store
    try:
        # search_vectors returns qdrant point structs with 'vector' under .vector or as list
        neighbors = search_vectors(query_vector=vector, top_k=top_k, filters={"min_likes": 10})
    except Exception as exc:
        logger.warning("Vector store retrieval failed: %s", exc)
        neighbors = []

    best = 0.0
    for n in neighbors:
        # qdrant client returns objects with .vector or dict-like
        vec = None
        if isinstance(n, dict):
            vec = n.get("vector")
        else:
            vec = getattr(n, "vector", None)
        if not vec:
            continue
        sim = _cosine(vector, vec)
        if sim > best:
            best = sim

    # Map cosine similarity (-1..1) to 0..100 scale conservatively
    score = max(0.0, min(100.0, (best + 1.0) / 2.0 * 100.0))
    return float(score)


def groq_reward_score(text: str, groq_callable) -> float:
    """Ask the generation backend to score a single tweet on a 0-100 scale.

    `groq_callable(prompt)` must be a function that returns text output from the model.
    This function builds a concise scoring prompt and attempts to parse a numeric score.
    """
    if not text:
        return 0.0

    prompt = (
        "Rate the following X (Twitter) post from 0 to 100 for engagement potential,\n"
        " originality, and topic relevance. Reply with a single integer number only.\n\n"
        f"Post:\n{text}\n\nScore:" 
    )

    try:
        raw = groq_callable(prompt)
        if not raw:
            return 0.0
        raw = str(raw).strip()
        # extract first integer
        import re

        m = re.search(r"(\d{1,3})", raw)
        if m:
            val = int(m.group(1))
            return float(max(0, min(100, val)))
    except Exception as exc:
        logger.warning("Groq reward scoring failed: %s", exc)
    return 0.0


def combined_reward_score(db, text: str, groq_callable=None) -> float:
    """Combine semantic similarity and optional groq pass into a single 0-100 score.

    If `groq_callable` is provided, use a weighted average (70% groq, 30% semantic).
    Otherwise return the semantic score.
    """
    semantic = semantic_similarity_to_top_posts(db, text, top_k=8)
    if groq_callable:
        groq_score = groq_reward_score(text, groq_callable)
        # weighted average: groq 0.7, semantic 0.3
        return float(round((0.7 * groq_score) + (0.3 * semantic), 2))
    return semantic
