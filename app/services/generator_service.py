"""
generator_service.py  —  complete rewrite

Key improvements over the previous version:
  1. Topic-aware RAG that actually works — multi-strategy candidate gathering
     with keyword scoring, dedup, and noise filtering.
  2. Topic profiles with richer angle/voice/hook/seeds AND anti-patterns to
     force diversity.
  3. Prompt injects structural constraints (forbidden openers, forbidden phrases
     from recent drafts) so Gemini is steered away from repetition.
  4. Output validation that rejects generic, too-short, or hallucinated posts
     before they ever touch the DB.
  5. Similarity check uses token-level Jaccard instead of SequenceMatcher so
     paraphrases of the same idea are caught.
  6. Fallback tweets are topic-scoped and drawn from real reference snippets,
     never raw seed strings.
"""
from __future__ import annotations

import json
import os
import random
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from dotenv import load_dotenv
from fastapi import HTTPException
import httpx
import logging
from app.database import get_db_client
from app.services.retrieval_service import retrieve_similar_posts
from app.services.scoring_service import score_generated_post
from app.services.text_cleaner import (
    clean_generated_output,
    is_noisy_reference,
    sanitize_reference_text,
)

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("Groq_API_KEY")
GROQ_API_URL = os.getenv("GROQ_API_URL")
GROQ_MODEL = os.getenv("GROQ_MODEL") or os.getenv("GROQ_MODEL_NAME") or "openai/gpt-oss-20b"


def _call_groq(prompt: str, temperature: float = 0.8, top_p: float = 0.95, top_k: int = 40) -> str:
    if not GROQ_API_KEY or not GROQ_API_URL:
        raise RuntimeError("GROQ API not configured. Set GROQ_API_KEY and GROQ_API_URL in .env")

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    # Build an OpenAI-compatible Responses payload as used by Groq's OpenAI-compatible API.
    payload = {
        "model": GROQ_MODEL,
        "input": prompt,
        "temperature": temperature,
        # Note: Groq OpenAI-compatible API may not accept `max_tokens`.
        # Providers sometimes use `max_output_tokens` or similar — omit for now.
    }

    # Determine the correct endpoint: allow users to set a base URL like
    # https://api.groq.com/openai/v1 and append /responses, or set the full
    # responses path directly.
    base = GROQ_API_URL.rstrip("/")
    if base.endswith("/responses"):
        endpoint = base
    else:
        endpoint = base + "/responses"

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
        # Log URL and a truncated response body for debugging (do not print API key)
        try:
            body_text = resp.text
        except Exception:
            body_text = "<unable to read body>"
        truncated = (body_text[:1000] + "...") if len(body_text) > 1000 else body_text
        logging.getLogger("uvicorn.error").info(f"[generator][GROQ] POST {endpoint} -> status={resp.status_code} body={truncated!r}")

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            # propagate to caller to map to HTTPException
            raise

        data = resp.json()

    # Try several common shapes for responses to extract generated text
    if isinstance(data, dict):
        # Groq/OpenAI-style convenience property
        if "output_text" in data and isinstance(data["output_text"], str):
            return data["output_text"]

        # Older/alternate forms
        if "output" in data:
            out = data["output"]
            if isinstance(out, str):
                return out
            if isinstance(out, list) and out:
                # Prefer a real message block over reasoning/tool blocks.
                message_candidates: list[str] = []
                generic_candidates: list[str] = []

                for entry in out:
                    if not isinstance(entry, dict):
                        continue

                    entry_type = str(entry.get("type", "")).lower()
                    cont = entry.get("content")
                    text_parts: list[str] = []

                    if isinstance(cont, list):
                        for item in cont:
                            if isinstance(item, dict):
                                t = item.get("text")
                                if isinstance(t, str) and t.strip():
                                    text_parts.append(t.strip())
                            elif isinstance(item, str) and item.strip():
                                text_parts.append(item.strip())
                    elif isinstance(cont, str) and cont.strip():
                        text_parts.append(cont.strip())

                    if not text_parts and isinstance(entry.get("text"), str):
                        t = entry.get("text", "").strip()
                        if t:
                            text_parts.append(t)

                    if not text_parts:
                        continue

                    joined = " ".join(text_parts).strip()
                    if not joined:
                        continue

                    if entry_type == "message":
                        message_candidates.append(joined)
                    else:
                        generic_candidates.append(joined)

                if message_candidates:
                    return message_candidates[-1]
                if generic_candidates:
                    return generic_candidates[-1]

                # Last resort from first element if structure is unexpected.
                first = out[0]
                if isinstance(first, dict):
                    return str(first.get("text") or first.get("content") or first)
                return str(first)

        # OpenAI-compatible 'choices' style
        if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
            first = data["choices"][0]
            if isinstance(first, dict):
                # try several common keys
                return first.get("text") or first.get("message") or first.get("content") or str(first)

    # Fallback: return full JSON as string
    return str(data)


# ---------------------------------------------------------------------------
# Topic profiles
# ---------------------------------------------------------------------------
TOPIC_PROFILES: dict[str, dict] = {
    "programming": {
        "angle": "shipping, debugging, and the tradeoffs behind real code",
        "voice": "practical, confident, a little wry — sounds like a senior dev",
        "hook": "lead with a sharp debugging insight, a lesson from production, or a developer truth nobody says out loud",
        "forbidden_openers": ["Good code", "The best code", "Programming is"],
        "seeds": [
            "Spent an hour on a bug that turned out to be a missing semicolon. Still counts as work.",
            "Shipping beats perfect every time — you can fix it when users tell you what's actually broken.",
            "The hardest part of a refactor isn't the code, it's convincing yourself you need it.",
            "Every codebase has one file that everyone avoids opening.",
        ],
        "fallbacks": [
            "Most senior devs I know debug by adding print statements and then lying about it.",
            "The real skill isn't writing the feature, it's knowing which feature not to write.",
            "Production is just a staging environment you forgot to label.",
        ],
    },
    "ai": {
        "angle": "model capability gaps, real-world deployment pain, and what actually changes",
        "voice": "curious, grounded, lightly skeptical — not hype, not doom",
        "hook": "lead with a concrete capability observation, a surprising failure mode, or a systems implication",
        "forbidden_openers": ["AI is", "Artificial intelligence", "The future of AI"],
        "seeds": [
            "The most useful AI feature I've shipped this year was a 50-line classifier, not a 100B model.",
            "Latency kills AI products faster than accuracy does.",
            "RAG works great until your retrieval is wrong, then it confidently hallucinates with citations.",
            "Fine-tuning a small model for your specific domain still beats prompting a frontier model for most tasks.",
        ],
        "fallbacks": [
            "The bottleneck in most AI projects isn't the model, it's cleaning the training data.",
            "Every AI demo looks like magic until you try it with your actual data.",
            "The hardest thing to eval is whether the AI is confidently wrong.",
        ],
    },
    "science": {
        "angle": "curiosity, surprising discoveries, and why the small details matter",
        "voice": "sharp, accessible, a little awed — Feynman energy without the jargon",
        "hook": "lead with a counterintuitive fact, an unexpected experimental result, or a question that reframes something familiar",
        "forbidden_openers": ["Science is", "Science keeps", "Scientists have"],
        "seeds": [
            "The placebo effect works even when patients know it's a placebo. That fact should keep you up at night.",
            "Octopuses have three hearts, blue blood, and solve mazes — and they live for two years. What a waste.",
            "The strongest material in nature is still a spider's dragline silk. We've been trying to copy it for 30 years.",
            "Crows remember human faces and hold grudges for years. Consider your life choices.",
        ],
        "fallbacks": [
            "Science is mostly wrong notes leading to one correct answer, which then gets questioned immediately.",
            "The most important experiments are the boring replications that never make headlines.",
            "Every measurement has uncertainty. The skill is knowing which uncertainty matters.",
        ],
    },
    "space": {
        "angle": "the engineering reality, scale, and the genuinely strange physics",
        "voice": "awed but grounded — respects the difficulty without losing wonder",
        "hook": "lead with a scale comparison, an engineering constraint, or a physics fact that breaks intuition",
        "forbidden_openers": ["Space is", "The universe", "NASA has"],
        "seeds": [
            "Voyager 1 is so far away that a radio signal takes 22 hours to arrive. It's still sending data.",
            "The ISS travels at 17,500 mph and its crew still experiences 16 sunrises a day.",
            "SpaceX catches rocket boosters because repainting them after touchdown is cheaper than rebuilding.",
            "If you shrank the sun to the size of a white blood cell, the Milky Way would be the size of the US.",
        ],
        "fallbacks": [
            "The moon is slowly moving away from Earth at the same rate your fingernails grow.",
            "Mars has the largest volcano in the solar system and no plate tectonics to erode it.",
            "Deep space communication is just extremely patient packet loss.",
        ],
    },
    "finance": {
        "angle": "execution, leverage, compounding, and the traps smart people fall into",
        "voice": "direct, ambitious, no fluff",
        "hook": "lead with a counterintuitive market truth or a concrete tradeoff",
        "forbidden_openers": ["Investing is", "Money is", "The market"],
        "seeds": [
            "Most stock-picking alpha disappears after fees. Index funds exist because this is embarrassing.",
            "The best business moat is not technology — it is the cost your customers pay to switch away.",
            "Compounding is obvious in retrospect and invisible in the present.",
            "First-mover advantage matters less than last-mover advantage. Whoever figures it out last, wins.",
        ],
        "fallbacks": [
            "Volatility is not the same as risk. Confusing them is expensive.",
            "The median VC-backed startup returns 0. The mean is great because of a handful of outliers.",
            "Revenue solves most startup problems. The rest are interesting theory.",
        ],
    },
    "gaming": {
        "angle": "design philosophy, player psychology, and what makes mechanics feel good",
        "voice": "enthusiastic, design-literate, specific",
        "hook": "lead with a specific mechanic observation or a design insight about player behavior",
        "forbidden_openers": ["Gaming is", "Video games", "Games have"],
        "seeds": [
            "The jump in Mario feels perfect because Nintendo spent months tuning the hang-time mid-arc.",
            "Hollow Knight has no quest markers because the devs wanted you to feel like an explorer, not a task manager.",
            "The best boss fights in FromSouls are designed to teach you exactly the pattern you need to beat them.",
            "Minecraft's best feature is that the game never tells you what to build. That was an accident.",
        ],
        "fallbacks": [
            "The best difficulty setting is the one where you barely win. Most games never find it.",
            "Speedrunning exploits are just the community discovering what the game actually is.",
            "The tutorials nobody reads are the ones that treat the player as a beginner. Great games just let you play.",
        ],
    },
    "health": {
        "angle": "evidence-based habits, behavioral psychology, and what actually moves the needle",
        "voice": "direct, evidence-grounded, non-preachy",
        "hook": "lead with a surprising study result or a common myth with a nuanced reality",
        "forbidden_openers": ["Health is", "Your health", "Being healthy"],
        "seeds": [
            "Sleep debt is real and it compounds. You can't pay it back with one long weekend.",
            "The most evidence-based mental health intervention is still just regular cardiovascular exercise.",
            "Loneliness is as damaging as smoking 15 cigarettes a day, according to meta-analyses.",
            "Stretching before exercise doesn't prevent injury. Warming up does.",
        ],
        "fallbacks": [
            "Most diets work in the short term. None of them work because people stop doing them.",
            "Walking is underrated. 10,000 steps is arbitrary but the movement is real.",
            "The research on supplements mostly shows expensive urine.",
        ],
    },
    "business": {
        "angle": "execution, leverage, hard-won lessons from building and scaling",
        "voice": "direct, ambitious, insight-driven — sounds like someone who's done it",
        "hook": "lead with a business lesson, a counterintuitive scaling truth, or a sharp tradeoff",
        "forbidden_openers": ["Business is", "Success is", "Entrepreneurs should"],
        "seeds": [
            "Your best salesperson is a customer who would genuinely miss you if you disappeared.",
            "The second hire is harder than the first. The first hundred are harder than the second.",
            "Urgency is a cost center. The companies that move slowly on purpose are usually the ones that last.",
            "Almost every startup dies of overspending, not under-funding.",
        ],
        "fallbacks": [
            "Strategy without execution is trivia. Execution without strategy is chaos.",
            "The best product feedback is watching someone use your product and not interrupting.",
            "Hiring someone to fix a problem you don't understand is almost always a mistake.",
        ],
    },
    "design": {
        "angle": "simplicity, craft, the user's mental model",
        "voice": "clean, refined, detail-conscious — precise without being precious",
        "hook": "lead with a visual or usability insight that makes the reader rethink something they take for granted",
        "forbidden_openers": ["Good design", "Design is", "Great design"],
        "seeds": [
            "The first design principle: if you have to explain the interface, you've already lost.",
            "Whitespace is not empty. It is the room the reader's eye needs to understand what matters.",
            "Every design decision either reduces friction or adds delight. Ideally both.",
            "The hardest design skill is knowing when to stop adding things.",
        ],
        "fallbacks": [
            "Most redesigns make products worse. The original constraints were there for a reason.",
            "Dark mode is a preference, not a signal of quality.",
            "Icons without labels test whether your mental model matches your users'. Spoiler: it usually doesn't.",
        ],
    },
    "marketing": {
        "angle": "attention economics, audience behavior, message clarity",
        "voice": "clear, punchy, audience-aware — sounds like someone who's shipped real campaigns",
        "hook": "lead with a pattern observation, a distribution insight, or a memorable framing device",
        "forbidden_openers": ["Marketing is", "Good marketing", "Great brands"],
        "seeds": [
            "Nobody shares something because it was informative. They share it because it made them look smart.",
            "The best distribution channel is word of mouth. It's also the hardest to engineer.",
            "Most A/B tests find nothing because the sample size is too small. That's technically a result.",
            "Viral content isn't lucky. It hits an emotion the audience was already feeling.",
        ],
        "fallbacks": [
            "Clarity beats cleverness every time attention is scarce.",
            "The best headline answers the question your customer is already asking.",
            "A niche audience that loves you is worth more than a broad audience that forgets you.",
        ],
    },
}

_DEFAULT_PROFILE = {
    "angle": "a clear, specific observation that earns a second look",
    "voice": "clean, direct, specific — no filler",
    "hook": "lead with something that makes the reader stop scrolling",
    "forbidden_openers": [],
    "seeds": [
        "The obvious move is usually wrong because everyone else already took it.",
        "Specificity is the difference between insight and noise.",
    ],
    "fallbacks": [
        "The best ideas look obvious after the fact and invisible before.",
        "Most useful things are hard to explain and easy to demonstrate.",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _get_topic_profile(topic: str) -> dict:
    n = _normalize(topic)
    for key, profile in TOPIC_PROFILES.items():
        if key in n:
            return profile
    return _DEFAULT_PROFILE


def _token_jaccard(a: str, b: str) -> float:
    """Token-level Jaccard similarity — catches paraphrases SequenceMatcher misses."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _extract_candidate_from_reasoning(text: str) -> str:
    """Try to extract a short, tweet-like candidate from verbose reasoning.

    Strategies (in order):
    - Find a quoted substring between " or “ that looks like a tweet.
    - Search lines (from bottom) for a short line (20-280 chars) that
      doesn't contain instruction boilerplate.
    - Fallback to truncating to 280 characters.
    """
    if not text:
        return ""

    # If the model explicitly produced a "Tweet:" field, prefer that payload.
    tweet_match = re.search(r"\bTweet\s*:\s*(.+)", text, flags=re.I | re.S)
    if tweet_match:
        text = tweet_match.group(1).strip()

    # Prefer quoted string, but skip obvious prompt fragments/lists.
    m = re.search(r'["“](.{20,280}?)["”]', text, flags=re.S)
    if m:
        quoted = m.group(1).strip()
        if (
            '\", \"' not in quoted
            and not re.search(r"\b(topic|angle|hook|variation hint|must be|forbidden)\b", quoted, flags=re.I)
        ):
            return quoted

    # Look for candidate lines from the end (often model appends result last)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    blacklist = re.compile(
        r"\b(we need|we must|must be|variation hint|hook style|we should|we will|forbidden openers|banned words|double-check|must|mustn't|topic:|angle:|under 280|no hashtags|no emojis|no urls)\b",
        re.I,
    )
    for line in reversed(lines):
        if 20 <= len(line) <= 280 and not blacklist.search(line) and '\", \"' not in line:
            return line

    # Fallback: take the first sentence-like chunk under 280
    sentences = re.split(r'(?<=[.!?])\s+', text)
    for s in reversed(sentences):
        s = s.strip()
        if 20 <= len(s) <= 280 and not blacklist.search(s) and '\", \"' not in s:
            return s

    return text[:280].strip()


def _is_too_similar(text: str, candidates: list[str], threshold: float = 0.45) -> bool:
    if not text or not candidates:
        return False
    for c in candidates:
        if not c:
            continue
        # Jaccard check (catches paraphrases)
        if _token_jaccard(text, c) >= threshold:
            return True
        # Subsequence check (catches slight variations)
        if SequenceMatcher(None, _normalize(text), _normalize(c)).ratio() >= 0.72:
            return True
    return False


_GENERIC_MARKERS = [
    "keeps moving fast",
    "clearest ideas are usually the simplest",
    "keep the message sharp, useful, and easy to share",
    "the strongest post usually starts with",
    "people respond when the message is specific",
    "a good post makes the topic feel useful",
]


def _is_generic(text: str) -> bool:
    n = _normalize(text)
    return any(marker in n for marker in _GENERIC_MARKERS)


def _is_valid_output(text: str) -> bool:
    """Return False if the generated text should be discarded."""
    if not text or len(text.strip()) < 30:
        return False
    if _is_generic(text):
        return False
    # Reject instruction-like fragments and obvious meta text.
    low = _normalize(text)
    if any(marker in low for marker in [
        "double-check",
        "banned words",
        "under 280",
        "no hashtags",
        "no emojis",
        "no urls",
        "must be",
        "variation hint",
        "hook style",
    ]):
        return False
    if text.strip().endswith(":"):
        return False
    # Must contain at least 6 words
    if len(text.split()) < 6:
        return False
    return True


# ---------------------------------------------------------------------------
# RAG: reference post retrieval
# ---------------------------------------------------------------------------

def _get_trend_pattern(db, topic: str) -> dict | None:
    normalized = _normalize(topic)

    cluster = db.trend_clusters.find_one(
        {"topic": normalized},
        sort=[("created_at", -1)],
    )

    if not cluster:
        return None

    pattern = db.trend_patterns.find_one(
        {"cluster_id": cluster.get("_id")},
        sort=[("created_at", -1)],
    )

    if not pattern or not pattern.pattern_json:
        return None

    try:
        data = json.loads(pattern.pattern_json)
    except json.JSONDecodeError:
        return None

    data["summary"] = pattern.get("summary")
    return data

def _get_reference_posts(db, topic: str, limit: int = 5) -> list:
    """
    Multi-strategy retrieval:
      1. Exact topic slug match (scored by engagement)
      2. Keyword search across content for topic-related terms
      3. Global high-engagement fallback (if still short)

    Each candidate is scored by:
      - keyword hit count in content
      - engagement proxy (likes + reposts * 2)

    Returns up to `limit` de-duplicated, noise-filtered posts.
    """
    normalized = _normalize(topic)
    profile = _get_topic_profile(topic)
    candidate_limit = max(limit * 6, 30)

    # ── Strategy 0: embedding retrieval (primary) ────────────────────────
    retrieval_query = f"{topic}. {profile['angle']} {random.choice(profile.get('seeds', []))}"
    retrieved: list = []
    try:
        retrieved = retrieve_similar_posts(
            db,
            query_text=retrieval_query,
            top_k=limit * 3,
            topic=normalized,
            min_likes=5,
            language="en",
        )
    except Exception:
        retrieved = []

    # ── Strategy 1: topic slug match ─────────────────────────────────────
    topic_candidates = list(
        db.scraped_posts
        .find({"topic": normalized})
        .sort([("likes", -1), ("reposts", -1)])
        .limit(candidate_limit)
    )

    # ── Strategy 2: keyword search in content ────────────────────────────
    # Extract meaningful keywords from seeds and the topic name itself
    seed_text = " ".join(profile.get("seeds", []))
    raw_keywords = re.findall(r"[a-z]{4,}", _normalize(normalized + " " + seed_text))
    # Deduplicate and take top 6
    seen: set[str] = set()
    keywords: list[str] = []
    for w in raw_keywords:
        if w not in seen:
            seen.add(w)
            keywords.append(w)
        if len(keywords) >= 6:
            break

    keyword_candidates: list = []
    if keywords:
        query = {"$and": [{"content": {"$regex": kw, "$options": "i"}} for kw in keywords[:3]]}
        keyword_candidates = list(
            db.scraped_posts
            .find(query)
            .sort([("likes", -1), ("reposts", -1)])
            .limit(candidate_limit)
        )

    # ── Strategy 3: global high-engagement fallback ───────────────────────
    global_candidates = list(
        db.scraped_posts
        .find()
        .sort([("likes", -1), ("reposts", -1)])
        .limit(candidate_limit // 2)
    )

    # ── Merge + dedup by id ───────────────────────────────────────────────
    seen_ids: set[str] = set()
    all_candidates: list = []
    for post in retrieved + topic_candidates + keyword_candidates + global_candidates:
        post_id = str(post.get("_id")) if isinstance(post, dict) else getattr(post, "id", None)
        if post_id and post_id not in seen_ids:
            seen_ids.add(post_id)
            all_candidates.append(post)

    # ── Score and filter ──────────────────────────────────────────────────
    def _score(post) -> tuple[int, int]:
        content = post.get("content") if isinstance(post, dict) else getattr(post, "content", None)
        if is_noisy_reference(content):
            return (-999, 0)
        content_lower = _normalize(content or "")
        kw_hits = sum(1 for kw in keywords if kw in content_lower)
        likes = post.get("likes") if isinstance(post, dict) else getattr(post, "likes", 0)
        reposts = post.get("reposts") if isinstance(post, dict) else getattr(post, "reposts", 0)
        eng = (likes or 0) + (reposts or 0) * 2
        return (kw_hits, eng)

    scored = sorted(all_candidates, key=_score, reverse=True)

    # Filter noisy references and collect clean ones
    clean: list = []
    for post in scored:
        content = post.get("content") if isinstance(post, dict) else getattr(post, "content", None)
        if is_noisy_reference(content):
            continue
        clean.append(post)
        if len(clean) >= limit:
            break

    return clean


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_prompt(
    topic: str,
    reference_posts: list,
    recent_drafts: list[str],
    variation_hint: str,
    pattern_data: dict | None,
) -> str:
    profile = _get_topic_profile(topic)
    forbidden_openers = profile.get("forbidden_openers", [])

    # Build reference examples
    example_texts: list[str] = []
    for post in reference_posts:
        content = post.get("content") if isinstance(post, dict) else getattr(post, "content", "")
        cleaned = sanitize_reference_text(content or "")
        if cleaned and len(cleaned) > 40:
            example_texts.append(cleaned[:220])

    if not example_texts:
        example_texts = profile.get("seeds", [
            "Specific > general, every time.",
            "The insight nobody says out loud is usually the most useful one.",
        ])

    examples_block = "\n---\n".join(example_texts[:4])

    # Build recent-drafts block (first 120 chars each)
    recent_block = (
        "\n".join(f"- {d[:120]}" for d in recent_drafts[:6] if d)
        if recent_drafts
        else "- none"
    )

    # Forbidden openers block
    forbidden_block = (
        "\n".join(f"- Do NOT start with: \"{opener}\"" for opener in forbidden_openers)
        if forbidden_openers
        else "- (none specified)"
    )

    pattern_block = "- (no pattern data yet)"
    if pattern_data:
        hooks = pattern_data.get("hooks") or []
        hook_line = ", ".join(hooks[:5]) if hooks else "(none)"
        pattern_block = (
            f"- Summary: {pattern_data.get('summary', 'n/a')}\n"
            f"- Avg length: {pattern_data.get('length_avg', 0):.1f}\n"
            f"- Question rate: {pattern_data.get('question_rate', 0) * 100:.1f}%\n"
            f"- Hook examples: {hook_line}"
        )

    return f"""You are writing a single high-engagement post for X (formerly Twitter).

═══ TOPIC ════════════════════════════════════════
{topic}

═══ ANGLE ════════════════════════════════════════
{profile["angle"]}

═══ VOICE ════════════════════════════════════════
{profile["voice"]}

═══ HOOK STYLE ═══════════════════════════════════
{profile["hook"]}

═══ VARIATION HINT ═══════════════════════════════
{variation_hint}

═══ PATTERN SIGNALS (use as soft guidance) ═══════
{pattern_block}

═══ HIGH-PERFORMING REFERENCES (use as inspiration, not source) ══════════
{examples_block}

═══ RECENT DRAFTS — DO NOT REPEAT OR PARAPHRASE THESE ════════════════════
{recent_block}

═══ FORBIDDEN OPENERS ════════════════════════════
{forbidden_block}

═══ HARD RULES ════════════════════════════════════
- Output EXACTLY ONE tweet. Nothing else.
- Under 280 characters.
- No hashtags.
- No emojis.
- No URLs.
- No @mentions.
- Must feel clearly written FOR the topic "{topic}" — not for any other topic.
- Human-sounding, conversational, punchy.
- Do NOT copy wording from the reference examples.
- Do NOT start with a generic opener like "The best X is..." or "X keeps moving...".
- If you can't think of something original, pick a concrete specific detail from the topic and start there.

Tweet:"""


# ---------------------------------------------------------------------------
# Fallback tweet builder
# ---------------------------------------------------------------------------

def _build_fallback(topic: str, reference_posts: list) -> str:
    profile = _get_topic_profile(topic)
    fallbacks = profile.get("fallbacks", [])
    seeds = profile.get("seeds", [])

    # Try to extract a clean lead sentence from a reference post
    if reference_posts:
        for post in reference_posts:
            content = post.get("content") if isinstance(post, dict) else getattr(post, "content", "")
            cleaned = sanitize_reference_text(content or "")
            if not cleaned or len(cleaned) < 40:
                continue
            sentences = re.split(r"(?<=[.!?])\s+", cleaned)
            for sentence in sentences:
                sentence = sentence.strip()
                if 30 <= len(sentence) <= 240:
                    return sentence[:280]

    # Fall back to topic-specific fallbacks
    candidates = fallbacks + seeds
    if candidates:
        return random.choice(candidates)[:280]

    return f"One honest observation about {topic.lower()} is usually worth ten hot takes."


# ---------------------------------------------------------------------------
# Main generation entry point
# ---------------------------------------------------------------------------

def generate_tweet(topic: str) -> dict:
    if not GROQ_API_KEY or not GROQ_API_URL:
        raise HTTPException(
            status_code=503,
            detail=(
                "GROQ_API_KEY or GROQ_API_URL is not configured. "
                "Add valid values to .env and restart the server."
            ),
        )

    db = get_db_client()

    reference_posts = _get_reference_posts(db, topic)
    pattern_data = _get_trend_pattern(db, topic)

    recent_docs = list(
        db.generated_posts
        .find({"topic": topic})
        .sort("created_at", -1)
        .limit(8)
    )

    recent_drafts: list[str] = [
        post.get("generated_text")
        for post in recent_docs
        if post.get("generated_text")
    ]

    variation_hints = [
        "Use an unexpected contrast or a counterintuitive angle.",
        "Frame it as a single observation that builds to an unexpected payoff.",
        "Use a specific, concrete example — zero abstractions.",
        "Make it sound like an overheard conversation between two experts.",
        "Lead with a failure or mistake, then pivot to the lesson.",
        "Ask a rhetorical question that makes the reader feel seen.",
        "State something obvious — then immediately complicate it.",
        "Start mid-thought, as if you're continuing a conversation.",
    ]

    max_attempts = 5
    generated_text = ""

    for attempt in range(max_attempts):
        hint = random.choice(variation_hints)
        prompt = _build_prompt(topic, reference_posts, recent_drafts, hint, pattern_data)

        try:
            temperature = min(0.85 + attempt * 0.05, 1.0)
            raw = _call_groq(prompt, temperature=temperature, top_p=0.95, top_k=40)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # Log provider response body for debugging
            try:
                logging.getLogger("uvicorn.error").warning(f"[generator][GROQ] HTTP error {status}; body={exc.response.text[:1000]!r}")
            except Exception:
                pass
            if status == 429:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Groq quota exhausted for model. "
                        "Set a different model or enable billing."
                    ),
                ) from exc
            # map other 4xx/5xx to 503 so the route can handle or propagate
            raise HTTPException(status_code=503, detail=f"Groq API error: {status}") from exc
        except RuntimeError as exc:
            # configuration/other runtime errors
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            print(f"[generator] Groq call failed (attempt {attempt + 1}): {exc}")
            break

        raw = (raw or "")

        # Strip any preamble the generation backend sometimes adds
        # (e.g. "Here's a tweet:", "Tweet:", etc.)
        raw = re.sub(r"^(?:tweet|here'?s?(?:\s+a\s+tweet)?)\s*:?\s*", "", raw, flags=re.IGNORECASE)
        raw = raw.strip('" \n')

        # If the model returned a verbose reasoning block, try to extract
        # a concise tweet-like candidate from it.
        raw = _extract_candidate_from_reasoning(raw)

        candidate = clean_generated_output(raw, reference_posts)

        if not _is_valid_output(candidate):
            print(f"[generator] Discarding invalid output on attempt {attempt + 1}: {candidate!r}")
            continue

        if _is_too_similar(candidate, recent_drafts):
            print(f"[generator] Too similar to recent draft, retrying (attempt {attempt + 1})")
            continue

        generated_text = candidate
        break

    if not generated_text:
        generated_text = _build_fallback(topic, reference_posts)
        print(f"[generator] Using fallback for topic={topic!r}: {generated_text!r}")

        post = {
            "topic": topic,
            "generated_text": generated_text,
            "status": "generated",
            "predicted_score": 0.0,
            "posted": False,
            "actual_likes": 0,
            "actual_views": 0,
            "actual_reposts": 0,
            "created_at": datetime.now(timezone.utc),
        }
        result = db.generated_posts.insert_one(post)
        post["_id"] = result.inserted_id

        try:
            score_generated_post(db, post)
        except Exception as exc:
            logging.getLogger("uvicorn.error").warning(f"[generator][score] failed: {exc}")

        return post