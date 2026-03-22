"""Unit tests for LogBuffer and WorkerLogHandler."""

from __future__ import annotations

import asyncio
import logging

import pytest

from worker.log_buffer import LogBuffer, LogEntry, WorkerLogHandler

# ---------------------------------------------------------------------------
# LogBuffer tests
# ---------------------------------------------------------------------------


def test_append_stores_entry():
    buf = LogBuffer()
    buf.append("INFO", "hello world")
    recent = buf.get_recent()
    assert len(recent) == 1
    assert recent[0].level == "INFO"
    assert recent[0].message == "hello world"


def test_ring_buffer_evicts_oldest():
    buf = LogBuffer(maxlen=3)
    for i in range(5):
        buf.append("DEBUG", f"msg {i}")
    recent = buf.get_recent(10)
    assert len(recent) == 3
    assert [e.message for e in recent] == ["msg 2", "msg 3", "msg 4"]


def test_get_recent_limits_count():
    buf = LogBuffer()
    for i in range(10):
        buf.append("INFO", f"msg {i}")
    recent = buf.get_recent(3)
    assert len(recent) == 3
    assert [e.message for e in recent] == ["msg 7", "msg 8", "msg 9"]


def test_get_recent_returns_all_when_fewer():
    buf = LogBuffer()
    buf.append("INFO", "a")
    buf.append("INFO", "b")
    recent = buf.get_recent(100)
    assert len(recent) == 2


@pytest.mark.asyncio
async def test_subscriber_receives_entry():
    buf = LogBuffer()
    queue = buf.subscribe()
    buf.append("WARNING", "subscriber test")
    entry = queue.get_nowait()
    assert entry.level == "WARNING"
    assert entry.message == "subscriber test"


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive():
    buf = LogBuffer()
    q1 = buf.subscribe()
    q2 = buf.subscribe()
    buf.append("ERROR", "broadcast")
    e1 = q1.get_nowait()
    e2 = q2.get_nowait()
    assert e1.message == "broadcast"
    assert e2.message == "broadcast"


@pytest.mark.asyncio
async def test_full_queue_drops_without_error():
    from datetime import UTC, datetime

    buf = LogBuffer()
    # Create a subscriber with maxsize=1 and pre-fill it
    small_queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=1)
    buf._subscribers.add(small_queue)
    filler = LogEntry(timestamp=datetime.now(UTC), level="INFO", message="pre-fill")
    small_queue.put_nowait(filler)
    # This should not raise even though queue is full
    buf.append("INFO", "should drop")
    assert buf.get_recent()[0].message == "should drop"


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    buf = LogBuffer()
    queue = buf.subscribe()
    buf.unsubscribe(queue)
    buf.append("INFO", "after unsubscribe")
    assert queue.empty()


# ---------------------------------------------------------------------------
# WorkerLogHandler tests
# ---------------------------------------------------------------------------


def test_handler_emits_to_buffer():
    buf = LogBuffer()
    handler = WorkerLogHandler(buf)
    test_logger = logging.getLogger("test_handler_emits_to_buffer")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)
    try:
        test_logger.info("test message")
        recent = buf.get_recent()
        assert len(recent) == 1
        assert recent[0].level == "INFO"
        assert "test message" in recent[0].message
    finally:
        test_logger.removeHandler(handler)
