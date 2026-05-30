from __future__ import annotations

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.event_broadcaster import subscribe

router = APIRouter()


async def _event_generator(request: Request):
    async for event in subscribe():
        if await request.is_disconnected():
            break
        data = json.dumps(event, default=str)
        yield f"data: {data}\n\n"


@router.get("/events/stream")
async def events_stream(request: Request):
    return StreamingResponse(_event_generator(request), media_type="text/event-stream")
