"""SSE endpoint for real-time worker log streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/worker")


@router.get("/logs")
async def worker_logs(request: Request) -> EventSourceResponse:
    log_buffer = request.app.state.worker_service.log_buffer

    async def event_generator() -> AsyncGenerator[dict[str, str], None]:
        entries = log_buffer.get_recent(500)
        yield {
            "event": "worker_log_batch",
            "data": json.dumps(
                [
                    {
                        "timestamp": entry.timestamp.isoformat(),
                        "level": entry.level,
                        "message": entry.message,
                    }
                    for entry in entries
                ]
            ),
        }

        queue = log_buffer.subscribe()
        try:
            while True:
                entry = await queue.get()
                yield {
                    "event": "worker_log",
                    "data": json.dumps(
                        {
                            "timestamp": entry.timestamp.isoformat(),
                            "level": entry.level,
                            "message": entry.message,
                        }
                    ),
                }
        except asyncio.CancelledError:
            pass
        finally:
            log_buffer.unsubscribe(queue)

    return EventSourceResponse(event_generator())
