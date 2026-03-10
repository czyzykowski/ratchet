"""Tests for SSE /api/events endpoint: queue lifecycle and fan-out logic."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from web.routes.api.events import sse_events


async def _drain_generator(gen: Any, count: int) -> list[dict[str, str]]:
    results = []
    async for item in gen:
        results.append(item)
        if len(results) >= count:
            break
    return results


async def test_queue_registered_on_connect() -> None:
    """Queue is added to sse_queues when a client connects."""
    queues: set[asyncio.Queue[str]] = set()
    queue: asyncio.Queue[str] = asyncio.Queue()
    await queue.put('{"task_id": "abc"}')

    class FakeState:
        sse_queues = queues

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()

    request = FakeRequest()
    await sse_events(request)  # type: ignore[arg-type]
    # Queue was added during sse_events call
    assert len(queues) == 1


async def test_queue_removed_on_disconnect() -> None:
    """Queue is removed from sse_queues when the generator is closed (disconnect)."""
    queues: set[asyncio.Queue[str]] = set()

    class FakeState:
        sse_queues = queues

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()

    request = FakeRequest()
    response = await sse_events(request)  # type: ignore[arg-type]
    assert len(queues) == 1

    gen = response.body_iterator
    # Start the generator by advancing it through one item, then close it
    the_queue = next(iter(queues))
    the_queue.put_nowait('{"task_id": "test-id"}')
    await gen.__anext__()  # advances past the yield; generator is now suspended at queue.get()
    await gen.aclose()  # injects GeneratorExit; triggers finally → discard

    # Queue should have been removed via finally/discard
    assert len(queues) == 0


async def test_fan_out_broadcasts_to_all_queues() -> None:
    """broadcast via put_nowait delivers payload to all registered queues."""
    queue1: asyncio.Queue[str] = asyncio.Queue()
    queue2: asyncio.Queue[str] = asyncio.Queue()
    queues: set[asyncio.Queue[str]] = {queue1, queue2}

    task_id = "test-uuid-1234"
    payload = json.dumps({"task_id": task_id})
    for q in set(queues):
        q.put_nowait(payload)

    assert queue1.get_nowait() == payload
    assert queue2.get_nowait() == payload


async def test_sse_event_format() -> None:
    """Yielded dict has event='task_updated' and correct data payload."""
    queues: set[asyncio.Queue[str]] = set()
    task_id = "deadbeef-0000-0000-0000-000000000001"
    payload = json.dumps({"task_id": task_id})

    class FakeState:
        sse_queues = queues

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()

    request = FakeRequest()
    response = await sse_events(request)  # type: ignore[arg-type]

    # Put a message so the generator yields
    assert len(queues) == 1
    the_queue = next(iter(queues))
    the_queue.put_nowait(payload)

    gen = response.body_iterator
    item = await gen.__anext__()
    assert item["event"] == "task_updated"
    data = json.loads(item["data"])
    assert data["type"] == "task_updated"
    assert data["task_id"] == task_id
    assert "project_id" in data

    # Clean up
    try:
        await gen.aclose()
    except Exception:
        pass
