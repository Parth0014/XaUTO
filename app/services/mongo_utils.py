from __future__ import annotations

from bson import ObjectId


def to_object_id(value: str | ObjectId | None) -> ObjectId | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def serialize_doc(doc: dict | None) -> dict | None:
    if not doc:
        return None
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


def serialize_docs(docs: list[dict]) -> list[dict]:
    return [serialize_doc(doc) for doc in docs if doc]
