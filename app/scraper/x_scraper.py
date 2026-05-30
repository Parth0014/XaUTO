import re
import hashlib
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from app.database import get_db_client
from app.services.scrape_progress import update_scrape_progress
from app.services.text_cleaner import sanitize_reference_text, normalize_content, detect_language_simple
from app.services.nlp_processor import (
    detect_topic,
    analyze_sentiments
)
from app.services.embedding_pipeline import embed_and_store_posts


def parse_metric(value):

    value = value.strip().upper()

    try:

        if "K" in value:
            return int(float(value.replace("K", "")) * 1000)

        elif "M" in value:
            return int(float(value.replace("M", "")) * 1000000)

        return int(value)

    except:
        return 0


def clean_text(text):

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def sanitize_content(text: str) -> str:
    return sanitize_reference_text(text)


def scrape_x_trends():

    db = get_db_client()
    max_runtime_seconds = 30 * 60
    delay_seconds = 15
    scrolls_per_cycle = 3
    deadline = time.monotonic() + max_runtime_seconds
    seen_hashes = set()
    cycle_number = 0
    total_inserted = 0

    with sync_playwright() as p:

        browser = p.chromium.connect_over_cdp(
            "http://127.0.0.1:9222"
        )

        context = browser.contexts[0]

        page = context.new_page()

        print("CONNECTED TO CHROME")

        page.goto("https://x.com/home")

        page.wait_for_selector("article")

        update_scrape_progress(
            state="running",
            message="Connected to X and waiting for tweet cards.",
            chrome="connected",
        )

        while time.monotonic() < deadline:
            cycle_number += 1

            # MULTIPLE SCROLLS
            for _ in range(scrolls_per_cycle):

                page.mouse.wheel(0, 5000)

                time.sleep(2)

            trend_elements = page.locator("article")

            count = trend_elements.count()

            print("TWEETS FOUND:", count)

            update_scrape_progress(
                cycle=cycle_number,
                seen=count,
                message=f"Cycle {cycle_number}: scanning {count} visible posts from the current X timeline.",
                last_error=None,
            )

            scraped_candidates = []

            for i in range(min(count, 20)):

                try:

                    trend = trend_elements.nth(i)

                    raw_text = trend.inner_text()

                    if not raw_text.strip():
                        continue

                    lines = [
                        clean_text(line)
                        for line in raw_text.split("\n")
                        if clean_text(line)
                    ]

                    if len(lines) < 4:
                        continue

                    # BASIC STRUCTURE
                    username = lines[0]

                    handle = ""

                    timestamp = ""

                    tweet_content = ""

                    # FIND HANDLE
                    for line in lines:

                        if line.startswith("@"):
                            handle = line

                        elif re.match(r"^\d+[smhdw]$", line):
                            timestamp = line

                    # REMOVE METRICS
                    filtered_lines = []

                    for line in lines:

                        if line == username:
                            continue

                        if line == handle:
                            continue

                        if line == timestamp:
                            continue

                        if re.match(r"^[\d\.]+[KMB]?$", line):
                            continue

                        filtered_lines.append(line)

                    # CONTENT
                    if filtered_lines:
                        tweet_content = " ".join(filtered_lines)

                    tweet_content = clean_text(tweet_content)

                    # METRICS
                    replies = 0
                    reposts = 0
                    likes = 0
                    views = 0

                    metric_candidates = [
                        line for line in lines
                        if re.match(r"^[\d\.]+[KMB]?$", line)
                    ]

                    if len(metric_candidates) >= 4:

                        replies = parse_metric(metric_candidates[-4])

                        reposts = parse_metric(metric_candidates[-3])

                        likes = parse_metric(metric_candidates[-2])

                        views = parse_metric(metric_candidates[-1])

                    # SKIP EMPTY
                    if not tweet_content:
                        continue

                    # sanitize content to remove UI noise before topic detection and storage
                    tweet_content = sanitize_content(tweet_content)

                    if not tweet_content:
                        continue

                    topic = detect_topic(tweet_content)

                    # DUPLICATE CHECK
                    normalized = normalize_content(tweet_content)

                    content_hash = hashlib.sha256(
                        normalized.encode("utf-8")
                    ).hexdigest()

                    if content_hash in seen_hashes:
                        continue

                        existing = db.scraped_posts.find_one({
                            "$or": [
                                {"content_hash": content_hash},
                                {"content": tweet_content},
                            ]
                        })

                        if existing:
                            print("DUPLICATE SKIPPED")
                            seen_hashes.add(content_hash)
                            continue

                        print("\n====================")
                        print("USERNAME:", username)
                        print("HANDLE:", handle)
                        print("TIME:", timestamp)
                        print("CONTENT:", tweet_content)
                        print("REPLIES:", replies)
                        print("REPOSTS:", reposts)
                        print("LIKES:", likes)
                        print("VIEWS:", views)
                        print("====================\n")

                        scraped_candidates.append({
                            "content_hash": content_hash,
                            "username": username,
                            "tweet_content": tweet_content,
                            "normalized_content": normalized,
                            "language": detect_language_simple(tweet_content),
                            "replies": replies,
                            "reposts": reposts,
                            "likes": likes,
                            "views": views,
                            "topic": topic
                        })

                    update_scrape_progress(
                        last_author=username,
                        last_topic=topic,
                        last_content=tweet_content[:180],
                        message=f"Captured {username} in topic {topic or 'unknown'}.",
                    )

                except Exception as e:

                    print("ERROR:", e)
                    update_scrape_progress(
                        last_error=str(e),
                        message="A tweet card could not be parsed, continuing.",
                    )

            sentiments = analyze_sentiments(
                [candidate["tweet_content"] for candidate in scraped_candidates]
            )

            inserted_count = 0

            new_posts = []

            for candidate, sentiment in zip(scraped_candidates, sentiments):

                post = {
                    "platform": "x",
                    "author": candidate["username"],
                    "content": candidate["tweet_content"],
                    "normalized_content": candidate["normalized_content"],
                    "content_hash": candidate["content_hash"],
                    "language": candidate["language"],
                    "likes": candidate["likes"],
                    "replies": candidate["replies"],
                    "reposts": candidate["reposts"],
                    "views": candidate["views"],
                    "topic": candidate["topic"],
                    "sentiment": sentiment,
                    "created_at": datetime.now(timezone.utc),
                }

                new_posts.append(post)
                seen_hashes.add(candidate["content_hash"])
                inserted_count += 1
                total_inserted += 1

                update_scrape_progress(
                    inserted=total_inserted,
                    message=f"Inserted {total_inserted} posts so far. Latest cycle added {inserted_count}.",
                    last_author=candidate["username"],
                    last_topic=candidate["topic"],
                    last_content=candidate["tweet_content"][:180],
                )

                if new_posts:
                    result = db.scraped_posts.insert_many(new_posts)
                    for post, oid in zip(new_posts, result.inserted_ids):
                        post["_id"] = oid

                try:
                    embed_and_store_posts(db, new_posts)
                except Exception as error:
                    print("EMBEDDING ERROR:", error)

                print("CYCLE COMPLETE. INSERTED:", inserted_count)

                update_scrape_progress(
                    state="running",
                    message=f"Cycle {cycle_number} complete with {inserted_count} new posts.",
                    inserted=total_inserted,
                )

                if time.monotonic() >= deadline:
                    break

                time.sleep(delay_seconds)

        print("SCRAPING COMPLETE")