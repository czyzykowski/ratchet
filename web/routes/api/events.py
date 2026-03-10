"""SSE endpoint for real-time task state change notifications."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/events")
async def sse_events(request: Request) -> EventSourceResponse:
    queue: asyncio.Queue[str] = asyncio.Queue()
    request.app.state.sse_queues.add(queue)

    async def event_generator() -> AsyncGenerator[dict[str, str], None]:
        try:
            while True:
                data = await queue.get()
                yield {"event": "task_updated", "data": data}
        except asyncio.CancelledError:
            pass
        finally:
            request.app.state.sse_queues.discard(queue)

    return EventSourceResponse(event_generator())
