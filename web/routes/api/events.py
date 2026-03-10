"""SSE endpoint for real-time task state change notifications."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api", tags=["api"])


async def _get_project_id(pool: object, task_id: str) -> str | None:
    """Query current_tasks view for project_id. Returns None on any error."""
    try:
        async with pool.connection() as conn:  # type: ignore[attr-defined]
            row = await (
                await conn.execute(
                    "SELECT project_id FROM current_tasks WHERE id = %s::uuid",
                    (task_id,),
                )
            ).fetchone()
            if row:
                return str(row[0])
    except Exception:
        pass
    return None


@router.get("/events")
async def sse_events(request: Request) -> EventSourceResponse:
    queue: asyncio.Queue[str] = asyncio.Queue()
    request.app.state.sse_queues.add(queue)

    async def event_generator() -> AsyncGenerator[dict[str, str], None]:
        try:
            while True:
                raw = await queue.get()
                try:
                    payload = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    payload = {}
                task_id: str | None = payload.get("task_id")
                project_id: str | None = None
                if task_id:
                    try:
                        UUID(task_id)
                        pool = getattr(request.app.state, "pool", None)
                        if pool is not None:
                            project_id = await _get_project_id(pool, task_id)
                    except ValueError:
                        pass
                event_data = json.dumps(
                    {
                        "type": "task_updated",
                        "task_id": task_id,
                        "project_id": project_id,
                    }
                )
                yield {"event": "task_updated", "data": event_data}
        except asyncio.CancelledError:
            pass
        finally:
            request.app.state.sse_queues.discard(queue)

    return EventSourceResponse(event_generator())
