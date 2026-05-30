from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger("uvicorn.error")

_subscribers: list[asyncio.Queue] = []
_event_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop | None):
    global _event_loop
    _event_loop = loop


def _safe_put(q: asyncio.Queue, payload: Dict[str, Any]):
    try:
        q.put_nowait(payload)
    except Exception as exc:
        logger.debug("Failed to put to subscriber queue: %s", exc)


def publish_sync(event: Dict[str, Any]):
    """Publish an event from sync code into all subscriber queues."""
    loop = _event_loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("No event loop available; dropping event: %s", event)
            return

    for q in list(_subscribers):
        try:
            loop.call_soon_threadsafe(_safe_put, q, event)
        except Exception as exc:
            logger.debug("Failed scheduling put for subscriber: %s", exc)


async def subscribe():
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.append(q)
    try:
        while True:
            event = await q.get()
            yield event
    finally:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def make_event(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": event_type, "payload": payload}
