from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

import httpx
from app.services.text_cleaner import is_noisy_reference, sanitize_reference_text

_URL_RE = re.compile(r"https?://\S+|\bx\.com/\S+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#\w+")
_PROMPTY_RE = re.compile(
    r"\b(we need|must be|under 280|no hashtags|no emojis|double-check|ask rhetoric)\b",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF]"
)


def _autopost_enabled() -> bool:
    value = os.getenv("AUTOPOST_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _post_mode() -> str:
    return os.getenv("POST_MODE", "dry_run").strip().lower()


def _min_score() -> float:
    return float(os.getenv("POST_MIN_SCORE", "70"))


def _min_interval_minutes() -> int:
    return int(os.getenv("POST_MIN_INTERVAL_MINUTES", "30"))


def _daily_limit() -> int:
    return int(os.getenv("POST_DAILY_LIMIT", "6"))


def _max_per_run() -> int:
    return int(os.getenv("POST_MAX_PER_RUN", "1"))


def _webhook_url() -> str | None:
    return os.getenv("POST_WEBHOOK_URL")


def _x_api_base() -> str:
    return os.getenv("X_API_BASE", "https://api.x.com").rstrip("/")


def _x_oauth_credentials() -> tuple[str | None, str | None, str | None, str | None]:
    return (
        os.getenv("X_API_KEY"),
        os.getenv("X_API_SECRET"),
        os.getenv("X_ACCESS_TOKEN"),
        os.getenv("X_ACCESS_TOKEN_SECRET"),
    )


def _percent_encode(value: str) -> str:
    return urllib.parse.quote(value, safe="~-._")


def _build_oauth1_header(method: str, url: str, body_params: dict[str, Any]) -> str:
    api_key, api_secret, access_token, access_secret = _x_oauth_credentials()
    if not api_key or not api_secret or not access_token or not access_secret:
        raise RuntimeError("X OAuth credentials are not configured")

    nonce = base64.b64encode(os.urandom(16)).decode("ascii").rstrip("=")
    timestamp = str(int(time.time()))

    oauth_params = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp,
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }

    signature_params = {**oauth_params}
    for key, value in body_params.items():
        signature_params[str(key)] = str(value)

    encoded_items = [
        (_percent_encode(k), _percent_encode(signature_params[k]))
        for k in sorted(signature_params.keys())
    ]
    param_string = "&".join(f"{k}={v}" for k, v in encoded_items)

    base_elems = [
        method.upper(),
        _percent_encode(url),
        _percent_encode(param_string),
    ]
    base_string = "&".join(base_elems)

    signing_key = f"{_percent_encode(api_secret)}&{_percent_encode(access_secret)}"
    signature = hmac.new(signing_key.encode("ascii"), base_string.encode("ascii"), hashlib.sha1).digest()
    oauth_params["oauth_signature"] = base64.b64encode(signature).decode("ascii")

    header_params = ", ".join(
        f"{_percent_encode(k)}=\"{_percent_encode(v)}\"" for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_params}"


def _is_safe_to_post(text: str) -> tuple[bool, str | None]:
    raw = text or ""
    cleaned = sanitize_reference_text(raw)

    if not cleaned or len(cleaned) < 50:
        return False, "too_short"
    if len(cleaned) > 280:
        return False, "too_long"
    if _URL_RE.search(raw):
        return False, "contains_url"
    if _HASHTAG_RE.search(raw):
        return False, "contains_hashtag"
    if _EMOJI_RE.search(raw):
        return False, "contains_emoji"
    if _PROMPTY_RE.search(raw):
        return False, "prompt_leak"
    if is_noisy_reference(raw):
        return False, "noisy_text"

    return True, None


def _within_rate_limits(db) -> tuple[bool, str | None]:
    now = datetime.now(timezone.utc)
    min_interval = timedelta(minutes=_min_interval_minutes())
    day_cutoff = now - timedelta(hours=24)

    last_post = db.generated_posts.find_one(
        {"posted": True},
        sort=[("posted_at", -1)]
    )

    if last_post and last_post.get("posted_at"):
        if now - last_post.get("posted_at") < min_interval:
            return False, "rate_limited"

    daily_count = db.generated_posts.count_documents({
        "posted": True,
        "posted_at": {"$gte": day_cutoff},
    })

    if daily_count >= _daily_limit():
        return False, "daily_limit_reached"

    return True, None


def _is_too_similar(db, text: str, threshold: float = 0.9) -> bool:
    cleaned = sanitize_reference_text(text or "").lower()
    if not cleaned:
        return True

    recent = list(
        db.generated_posts
        .find({"posted": True})
        .sort("posted_at", -1)
        .limit(20)
    )

    for post in recent:
        other = sanitize_reference_text(post.get("generated_text") or "").lower()
        if not other:
            continue
        if SequenceMatcher(None, cleaned, other).ratio() >= threshold:
            return True

    return False


def _select_candidate(db, statuses: list[str]) -> dict | None:
    min_score = _min_score()

    candidates = list(
        db.generated_posts
        .find({
            "status": {"$in": statuses},
            "$or": [{"posted": False}, {"posted": {"$exists": False}}],
            "predicted_score": {"$gte": min_score},
        })
        .sort([("predicted_score", -1), ("created_at", -1)])
        .limit(25)
    )

    for post in candidates:
        is_safe, _ = _is_safe_to_post(post.get("generated_text") or "")
        if not is_safe:
            continue
        if _is_too_similar(db, post.get("generated_text") or ""):
            continue
        return post

    return None


def _post_via_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    url = _webhook_url()
    if not url:
        raise RuntimeError("POST_WEBHOOK_URL is not configured")

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}

    return data


def _post_via_x_api(text: str) -> dict[str, Any]:
    url = f"{_x_api_base()}/2/tweets"
    payload = {"text": text}
    auth_header = _build_oauth1_header("POST", url, payload)

    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def run_posting_cycle(db, allow_manual: bool = False, require_approval: bool | None = None) -> dict[str, Any]:
    if not _autopost_enabled() and not allow_manual:
        return {"posted": 0, "reason": "autopost_disabled"}

    if require_approval is None:
        require_approval = not _autopost_enabled()

    ok, reason = _within_rate_limits(db)
    if not ok:
        return {"posted": 0, "reason": reason}

    max_posts = _max_per_run()
    posted = []
    failed = []

    statuses = ["approved"] if require_approval else ["generated", "approved"]

    for _ in range(max_posts):
        candidate = _select_candidate(db, statuses)
        if not candidate:
            break

        is_safe, unsafe_reason = _is_safe_to_post(candidate.get("generated_text") or "")
        if not is_safe:
            db.generated_posts.update_one(
                {"_id": candidate.get("_id")},
                {"$set": {"status": "failed", "post_error": unsafe_reason}}
            )
            failed.append({"id": str(candidate.get("_id")), "reason": unsafe_reason})
            continue

        if _is_too_similar(db, candidate.get("generated_text") or ""):
            db.generated_posts.update_one(
                {"_id": candidate.get("_id")},
                {"$set": {"status": "failed", "post_error": "too_similar"}}
            )
            failed.append({"id": str(candidate.get("_id")), "reason": "too_similar"})
            continue

        mode = _post_mode()
        now = datetime.now(timezone.utc)

        if mode == "dry_run":
            db.generated_posts.update_one(
                {"_id": candidate.get("_id")},
                {"$set": {
                    "status": "posted",
                    "posted": True,
                    "posted_at": now,
                    "post_external_id": "dry_run",
                    "post_error": None,
                }}
            )
            posted.append({"id": str(candidate.get("_id")), "mode": mode})
            continue

        try:
            if mode == "x_api":
                response = _post_via_x_api(candidate.get("generated_text") or "")
            else:
                payload = {
                    "text": candidate.get("generated_text"),
                    "topic": candidate.get("topic"),
                    "predicted_score": candidate.get("predicted_score"),
                    "generated_post_id": str(candidate.get("_id")),
                }
                response = _post_via_webhook(payload)
            external_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
            if not external_id and isinstance(response, dict):
                external_id = response.get("id") or response.get("post_id")
            db.generated_posts.update_one(
                {"_id": candidate.get("_id")},
                {"$set": {
                    "status": "posted",
                    "posted": True,
                    "posted_at": now,
                    "post_external_id": str(external_id or ""),
                    "post_error": None,
                }}
            )
            posted.append({"id": str(candidate.get("_id")), "mode": mode})
        except Exception as exc:
            db.generated_posts.update_one(
                {"_id": candidate.get("_id")},
                {"$set": {"status": "failed", "post_error": str(exc)}}
            )
            failed.append({"id": str(candidate.get("_id")), "reason": str(exc)})

    return {"posted": len(posted), "failed": failed, "items": posted}
