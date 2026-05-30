from collections import defaultdict


def get_top_posts(db, limit: int = 20):
    return list(
        db.scraped_posts
        .find({}, {"author": 1, "content": 1, "likes": 1, "views": 1, "topic": 1, "sentiment": 1})
        .sort("likes", -1)
        .limit(limit)
    )


def get_top_topics(db):
    results = db.scraped_posts.aggregate([
        {
            "$group": {
                "_id": {"$ifNull": ["$topic", "unknown"]},
                "count": {"$sum": 1},
            }
        }
    ])

    topic_count = defaultdict(int)
    for item in results:
        topic_count[item.get("_id") or "unknown"] = int(item.get("count") or 0)

    return dict(topic_count)


def get_sentiment_distribution(db):
    results = db.scraped_posts.aggregate([
        {
            "$group": {
                "_id": {"$ifNull": ["$sentiment", "unknown"]},
                "count": {"$sum": 1},
            }
        }
    ])

    sentiments = defaultdict(int)
    for item in results:
        sentiments[item.get("_id") or "unknown"] = int(item.get("count") or 0)

    return dict(sentiments)