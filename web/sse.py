"""SSE broadcast helper for Ratchet web."""

from __future__ import annotations

from fastapi import FastAPI


def broadcast_task_updated(app: FastAPI) -> None:
    """Put a task_updated message into every connected SSE client queue."""
    clients = getattr(app.state, "sse_clients", [])
    for queue in list(clients):
        queue.put_nowait("task_updated")
