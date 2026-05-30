"""
nlp_processor.py  —  improved topic detection + sentiment
"""
from __future__ import annotations

import re
from transformers import pipeline

_sentiment_pipeline = None


def get_sentiment_pipeline():
    global _sentiment_pipeline
    if _sentiment_pipeline is None:
        _sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model="distilbert/distilbert-base-uncased-finetuned-sst-2-english",
            revision="714eb0f",
        )
    return _sentiment_pipeline


# ---------------------------------------------------------------------------
# Topic taxonomy — ordered from most-specific to most-general so the first
# match wins.  Each entry is (canonical_slug, [keyword_patterns]).
# Patterns are matched as whole-word substrings (case-insensitive).
# ---------------------------------------------------------------------------
_TOPIC_RULES: list[tuple[str, list[str]]] = [
    # ── Technology sub-domains ────────────────────────────────────────────
    ("programming", [
        r"\bpython\b", r"\bjavascript\b", r"\btypescript\b", r"\brust\b",
        r"\bgo\b(?:lang)?", r"\bc\+\+", r"\bjava\b", r"\bkotlin\b",
        r"\bswift\b", r"\bphp\b", r"\bruby\b", r"\bdart\b",
        r"\breact\b", r"\bvue\b", r"\bangular\b", r"\bnext\.?js\b",
        r"\bnode\.?js\b", r"\bfastapi\b", r"\bdjango\b", r"\bflask\b",
        r"\bcod(e|ing|er|ers)\b", r"\bdevelop(er|ers|ment|ing)\b",
        r"\bsoftware engineer", r"\bgithub\b", r"\bgit\b",
        r"\bdebugg", r"\brefactor", r"\bpull request\b", r"\bci[/ ]cd\b",
        r"\bdocker\b", r"\bkubernetes\b", r"\bapi\b", r"\bbackend\b",
        r"\bfrontend\b", r"\bfull.?stack\b", r"\bopen.?source\b",
        r"\balgorithm", r"\bdata struct", r"\bcompiler\b", r"\bide\b",
        r"\bstack overflow\b", r"\bvibe cod",
    ]),
    ("ai", [
        r"\bartificial intelligence\b", r"\bmachine learning\b",
        r"\bdeep learning\b", r"\bneural net", r"\bllm\b", r"\bgpt\b",
        r"\bgemini\b", r"\bclaude\b", r"\bopenai\b", r"\banthropic\b",
        r"\bhugging.?face\b", r"\bstable diffusion\b", r"\bdiffusion model",
        r"\btransformer model\b", r"\bfine.?tun", r"\bembedding",
        r"\bai model\b", r"\blarge language\b", r"\bai agent\b",
        r"\bgenerative ai\b", r"\bagi\b", r"\bai tool", r"\bai system",
        r"\bprompt engineer", r"\brag\b", r"\bvector db\b",
        r"\bhumanoid robot", r"\boptimus robot\b", r"\bai chip",
    ]),
    ("science", [
        r"\bphysics\b", r"\bchemistry\b", r"\bbiology\b", r"\bastronomy\b",
        r"\bneuroscience\b", r"\bquantum\b", r"\bblack hole\b",
        r"\bclimate change\b", r"\bevolution\b", r"\bdna\b", r"\brna\b",
        r"\bcell(ular)?\b", r"\bexperiment\b", r"\bscientist\b",
        r"\bresearch paper\b", r"\bpeer.?review", r"\bspacetime\b",
        r"\brelativ", r"\bparticle\b", r"\bnucleus\b", r"\batom(ic)?\b",
        r"\bscientific method\b", r"\btheory of\b",
    ]),
    ("space", [
        r"\bnasa\b", r"\bspacex\b", r"\brocket lab\b", r"\bstarship\b",
        r"\borbit\b", r"\blaunch\b(?=.*rocket|.*spacex|.*nasa)",
        r"\bsatellite\b", r"\bmars\b", r"\bmoon\b(?!.*cricket|.*party)",
        r"\bjames webb\b", r"\biss\b", r"\binternational space station\b",
        r"\bstarlinkr\b", r"\bastronautr\b", r"\bspacewalk\b",
        r"\bsolar system\b", r"\buniverse\b", r"\bgalaxy\b",
        r"\bexoplanet\b", r"\btelescope\b",
    ]),
    ("finance", [
        r"\bstock(s)?\b", r"\bshare price\b", r"\bmarket cap\b",
        r"\bcrypto(currency)?\b", r"\bbitcoin\b", r"\bethereum\b",
        r"\bblockchain\b", r"\bwallet\b(?=.*crypto|.*coin)",
        r"\binvest(ment|ing|or)?\b", r"\bventure capital\b", r"\bvc\b",
        r"\bipo\b", r"\bstartup fund", r"\bvaluation\b",
        r"\binterest rate\b", r"\bfederal reserve\b", r"\bgdp\b",
        r"\binflation\b", r"\bbull market\b", r"\bbear market\b",
        r"\bhedge fund\b", r"\bportfolio\b", r"\bdividend\b",
    ]),
    ("gaming", [
        r"\bvideo game\b", r"\bgaming\b", r"\bgamer\b",
        r"\bxbox\b", r"\bplaystation\b", r"\bnintendo\b", r"\bswitch\b(?=.*game|.*nintendo)",
        r"\bsteam\b(?=.*game|.*valve)", r"\bpc gaming\b",
        r"\bfortnite\b", r"\bminecraft\b", r"\bcall of duty\b",
        r"\bleague of legends\b", r"\bcs2\b", r"\bcounter.?strike\b",
        r"\bindiegame\b", r"\bgamedev\b", r"\bunreal engine\b",
        r"\bunity\b(?=.*game)", r"\bspeedrun\b", r"\besport",
    ]),
    ("health", [
        r"\bhealth\b", r"\bmedical\b", r"\bdoctor\b", r"\bnurse\b",
        r"\bhospital\b", r"\bsurgery\b", r"\bvaccine\b",
        r"\bmental health\b", r"\btherapy\b", r"\bpsychology\b",
        r"\bnutrition\b", r"\bfitness\b", r"\bworkout\b",
        r"\bdiabetes\b", r"\bcancer\b", r"\bpandemic\b",
        r"\bclinical trial\b", r"\bfda\b", r"\bpharma\b",
    ]),
    ("business", [
        r"\bceo\b", r"\bcfo\b", r"\bstartup\b", r"\bfound(er|ing)\b",
        r"\bproduct.?market fit\b", r"\bgrowth hack", r"\bsaas\b",
        r"\bchurn\b", r"\barr\b", r"\bmrr\b", r"\bunit economics\b",
        r"\bscale.?up\b", r"\bboard of director", r"\bp&l\b",
        r"\bcustomer acquisition\b", r"\bgo.?to.?market\b",
        r"\bpivot\b(?=.*startup|.*product|.*business)",
        r"\bshutdown\b(?=.*company|.*startup)",
    ]),
    ("design", [
        r"\bui\b", r"\bux\b", r"\buser experience\b", r"\buser interface\b",
        r"\bfigma\b", r"\bsketch\b(?=.*design)", r"\bprototype\b",
        r"\btypography\b", r"\bcolor palette\b", r"\bwhitespace\b",
        r"\bdesign system\b", r"\bresponsive design\b",
        r"\baccessibility\b(?=.*design|.*web)", r"\bbrand(ing)?\b",
        r"\bicon(ography)?\b", r"\bwireframe\b",
    ]),
    ("marketing", [
        r"\bseo\b", r"\bcontent market", r"\bemail campaign\b",
        r"\bconversion rate\b", r"\bfunnel\b(?=.*market|.*sales)",
        r"\blanding page\b", r"\bcall to action\b", r"\bcta\b",
        r"\bcopywriting\b", r"\bbrand awareness\b",
        r"\binfluencer\b", r"\bviral\b(?=.*post|.*campaign|.*market)",
        r"\bpaid ads\b", r"\bgoogle ads\b", r"\bmeta ads\b",
    ]),
]

# Build compiled patterns once
_COMPILED_RULES: list[tuple[str, list[re.Pattern]]] = [
    (slug, [re.compile(pat, re.IGNORECASE) for pat in patterns])
    for slug, patterns in _TOPIC_RULES
]


def detect_topic(text: str) -> str:
    """
    Return the most specific matching topic slug, or 'general'.
    Scores each topic by number of distinct keyword hits and returns
    the highest-scoring one (ties broken by rule order = specificity).
    """
    if not text:
        return "general"

    best_slug = "general"
    best_score = 0

    for slug, patterns in _COMPILED_RULES:
        score = sum(1 for pat in patterns if pat.search(text))
        if score > best_score:
            best_score = score
            best_slug = slug

    return best_slug


def analyze_sentiments(texts: list[str]) -> list[str]:
    if not texts:
        return []
    try:
        pipe = get_sentiment_pipeline()
        results = pipe([t[:512] for t in texts])
        return [r["label"] for r in results]
    except Exception:
        return ["unknown"] * len(texts)