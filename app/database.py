import os
from typing import Generator

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "x_ai_system")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is not configured")

_client = MongoClient(MONGO_URI)
_db = _client[MONGO_DB]


def get_db() -> Generator:
    yield _db


def get_db_client():
    return _db


def init_indexes(db):
    db.scraped_posts.create_index("content_hash", unique=True, sparse=True)
    db.scraped_posts.create_index([("created_at", DESCENDING)])
    db.scraped_posts.create_index([("topic", ASCENDING)])

    db.embedding_records.create_index("scraped_post_id")
    db.embedding_records.create_index("vector_id")

    db.generated_posts.create_index([("created_at", DESCENDING)])
    db.generated_posts.create_index([("predicted_score", DESCENDING)])
    db.generated_posts.create_index([("posted", ASCENDING), ("posted_at", DESCENDING)])
    db.generated_posts.create_index([("status", ASCENDING)])

    db.trend_clusters.create_index([("created_at", DESCENDING)])
    db.trend_clusters.create_index([("topic", ASCENDING)])
    db.trend_cluster_items.create_index([("cluster_id", ASCENDING)])
    db.trend_patterns.create_index([("cluster_id", ASCENDING), ("created_at", DESCENDING)])

    db.analytics_events.create_index([("created_at", DESCENDING)])