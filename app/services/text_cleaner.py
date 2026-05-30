"""
text_cleaner.py  —  improved sanitization with stricter noise filters
"""
from __future__ import annotations

import re
import unicodedata


# ---------------------------------------------------------------------------
# Patterns that indicate UI / scraper artefacts in raw tweet text
# ---------------------------------------------------------------------------
_UI_NOISE_PATTERNS = [
    re.compile(r"Translated from \w+", re.IGNORECASE),
    re.compile(r"Show original", re.IGNORECASE),
    re.compile(r"https?://\S+"),
    re.compile(r"#\w+"),                          # hashtags
    re.compile(r"\b\d+:\d{2}(?:\s*/\s*\d+:\d{2})?\b"),  # timestamps like 0:14 / 1:00
    re.compile(r"[\u00B7\u2022\u2023\u2026]+"),   # bullets / ellipsis chars
    re.compile(r"\bShow more\b", re.IGNORECASE),
    re.compile(r"\bQuote\b\s*·?"),                # "Quote" retweet header
    re.compile(r"@\w+"),                          # @mentions
    re.compile(r"\b(?:RT|via)\b\s*@\w*"),         # RT boilerplate
    # stray metric suffixes that slip through (e.g. "2\.3K views")
    re.compile(r"\b\d+(?:\.\d+)?[KMB]?\s*(?:views?|likes?|reposts?|replies?)\b", re.IGNORECASE),
    # timestamps like "· 2h", "· May 21"
    re.compile(r"·\s*(?:\d+[smhdw]|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+)"),
    # "Fan account" watermark
    re.compile(r"\bFan account\b", re.IGNORECASE),
    # "Parody account"
    re.compile(r"\bParody account\b", re.IGNORECASE),
    # "Create your own with Grok"
    re.compile(r"Create your own with \w+", re.IGNORECASE),
    # stray video duration artefacts like "0:02 / 0:34"
    re.compile(r"\d+:\d{2}\s*/\s*\d+:\d{2}"),
]

# Phrases that make a reference unsuitable (should not appear in prompts)
_BLOCKLIST_PHRASES = [
    "show original",
    "translated from",
    "fan account",
    "parody account",
    "create your own with grok",
    "x.com/",
    "http",
]

# Minimum quality thresholds
_MIN_ALPHA_CHARS = 25
_MIN_LENGTH_AFTER_CLEAN = 50


_MOJIBAKE_REPLACEMENTS = {
    "â€™": "'",
    "â€˜": "'",
    "â€œ": '"',
    "â€\x9d": '"',
    "â€“": "-",
    "â€”": "-",
    "â€¦": "...",
}


def _repair_mojibake(text: str) -> str:
    """Attempt to repair common UTF-8/latin1 mojibake corruption."""
    if not text:
        return text

    if "â" not in text and "Ã" not in text:
        return text

    try:
        repaired = text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
        if repaired and (
            repaired.count("â") + repaired.count("Ã")
            < text.count("â") + text.count("Ã")
        ):
            return repaired
    except Exception:
        pass

    return text


def sanitize_reference_text(text: str) -> str:
    """
    Remove all UI noise from a scraped tweet and return clean prose.
    Returns an empty string if nothing meaningful remains.
    """
    if not text:
        return ""

    cleaned = text

    cleaned = _repair_mojibake(cleaned)

    # Fix common UTF-8 mojibake sequences that can appear in scraped/model text.
    for bad, good in _MOJIBAKE_REPLACEMENTS.items():
        cleaned = cleaned.replace(bad, good)

    # Remove stray mojibake artifacts that slip through (e.g. "â " or "200âms")
    cleaned = re.sub(r"â\s+", "", cleaned)
    cleaned = re.sub(r"â(?=[A-Za-z0-9])", "", cleaned)

    for pat in _UI_NOISE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)

    # Collapse repeated punctuation  e.g. "!!!" → "!"
    cleaned = re.sub(r"([!?.]){2,}", r"\1", cleaned)

    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Strip leading "· " artefact that slips through
    cleaned = re.sub(r"^[·•\-–]\s*", "", cleaned).strip()

    return cleaned


def normalize_content(text: str) -> str:
    """
    Create a normalized string suitable for hashing, dedupe, and embeddings.
    """
    if not text:
        return ""

    cleaned = sanitize_reference_text(text)
    cleaned = cleaned.lower()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def detect_language_simple(text: str) -> str:
    """
    Lightweight heuristic language detection to avoid extra dependencies.
    Returns "en" or "unknown".
    """
    if not text:
        return "unknown"

    alpha_count = sum(1 for ch in text if ch.isalpha())
    if alpha_count == 0:
        return "unknown"

    ascii_alpha = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    ratio = ascii_alpha / alpha_count

    return "en" if ratio >= 0.7 else "unknown"


def is_noisy_reference(text: str) -> bool:
    """
    Return True when the text should be excluded from RAG references.
    """
    if not text:
        return True

    lowered = text.lower()

    # Blocklist check
    for phrase in _BLOCKLIST_PHRASES:
        if phrase in lowered:
            return True

    sanitized = sanitize_reference_text(text)

    if len(sanitized) < _MIN_LENGTH_AFTER_CLEAN:
        return True

    alpha_count = sum(1 for ch in sanitized if ch.isalpha())
    if alpha_count < _MIN_ALPHA_CHARS:
        return True

    # Reject if mostly non-Latin / non-ASCII — indicates foreign-language post
    ascii_alpha = sum(1 for ch in sanitized if ch.isascii() and ch.isalpha())
    if alpha_count > 0 and (ascii_alpha / alpha_count) < 0.5:
        return True

    return False


def clean_generated_output(text: str, reference_posts=None) -> str:
    """
    Sanitize the raw model output:
      1. Strip UI noise
      2. Remove verbatim n-grams copied from references (>5 words)
      3. Trim to 280 chars
    """
    if not text:
        return ""

    cleaned = sanitize_reference_text(text)

    if reference_posts:
        for post in reference_posts:
            content = post.get("content") if isinstance(post, dict) else getattr(post, "content", "")
            snippet = sanitize_reference_text(content or "")
            if not snippet or len(snippet) < 30:
                continue
            words = snippet.split()
            removed = False
            for n in range(min(10, len(words)), 4, -1):
                for i in range(len(words) - n + 1):
                    ngram = " ".join(words[i : i + n])
                    if len(ngram) < 25:
                        continue
                    if ngram.lower() in cleaned.lower():
                        # Case-insensitive replace
                        pattern = re.compile(re.escape(ngram), re.IGNORECASE)
                        cleaned = pattern.sub("", cleaned).strip()
                        removed = True
                        break
                if removed:
                    break

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:280]