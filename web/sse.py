"""SSE broadcast helper for Ratchet web."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI


def get_sse_clients(app: FastAPI) -> list[asyncio.Queue[str]]:
    return app.state.sse_clients  # type: ignore[no-any-return]


def broadcast_task_updated(app: FastAPI) -> None:
    """Put a task_updated message into every connected SSE client queue."""
    clients = getattr(app.state, "sse_clients", [])
    for queue in list(clients):
        queue.put_nowait("task_updated")
