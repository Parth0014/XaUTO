from __future__ import annotations

import logging
import os

import httpx

GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("Groq_API_KEY")
GROQ_API_URL = os.getenv("GROQ_API_URL")
GROQ_MODEL = os.getenv("GROQ_MODEL") or os.getenv("GROQ_MODEL_NAME") or "openai/gpt-oss-20b"


def call_groq(prompt: str, temperature: float = 0.8, top_p: float = 0.95, top_k: int = 40) -> str:
    if not GROQ_API_KEY or not GROQ_API_URL:
        raise RuntimeError("GROQ API not configured. Set GROQ_API_KEY and GROQ_API_URL in .env")

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": GROQ_MODEL,
        "input": prompt,
        "temperature": temperature,
    }

    base = GROQ_API_URL.rstrip("/")
    if base.endswith("/responses"):
        endpoint = base
    else:
        endpoint = base + "/responses"

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
        try:
            body_text = resp.text
        except Exception:
            body_text = "<unable to read body>"
        truncated = (body_text[:1000] + "...") if len(body_text) > 1000 else body_text
        logging.getLogger("uvicorn.error").info(
            f"[generator][GROQ] POST {endpoint} -> status={resp.status_code} body={truncated!r}"
        )

        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, dict):
        if "output_text" in data and isinstance(data["output_text"], str):
            return data["output_text"]

        if "output" in data:
            out = data["output"]
            if isinstance(out, str):
                return out
            if isinstance(out, list) and out:
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
                    elif isinstance(cont, str) and cont.strip():
                        text_parts.append(cont.strip())

                    candidate = "\n".join(text_parts).strip()
                    if not candidate:
                        continue

                    if entry_type == "message":
                        message_candidates.append(candidate)
                    else:
                        generic_candidates.append(candidate)

                if message_candidates:
                    return "\n".join(message_candidates).strip()
                if generic_candidates:
                    return "\n".join(generic_candidates).strip()

        if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
            choice = data["choices"][0]
            if isinstance(choice, dict):
                msg = choice.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        return content
                text = choice.get("text")
                if isinstance(text, str):
                    return text

    return str(data)