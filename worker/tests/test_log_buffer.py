"""Unit tests for LogBuffer, WorkerLogHandler, and WorkerService log lifecycle."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from worker.log_buffer import LogBuffer, LogEntry, WorkerLogHandler
from worker.service import WorkerService

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


# ---------------------------------------------------------------------------
# WorkerService handler lifecycle tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_postgres_store():
    with patch("worker.service.PostgresStore") as m:
        yield m


@pytest.fixture()
def mock_invoker():
    with patch("worker.service.ClaudeCodeInvoker") as m:
        instance = MagicMock()
        instance.terminate = MagicMock()
        m.return_value = instance
        yield m, instance


def _make_service() -> WorkerService:
    pool = MagicMock()
    return WorkerService(pool=pool, dsn="postgresql://localhost/test")


def _blocking_loop_factory():
    fut: asyncio.Future = asyncio.Future()

    async def _loop(*args, **kwargs):
        await fut

    from unittest.mock import AsyncMock
    mock = AsyncMock(side_effect=_loop)
    return fut, mock


@pytest.mark.asyncio
async def test_handler_installed_on_start_removed_on_stop(mock_postgres_store, mock_invoker):
    fut, loop_mock = _blocking_loop_factory()
    with patch("worker.service.notification_loop", loop_mock):
        svc = _make_service()
        worker_logger = logging.getLogger("worker")

        await svc.start()
        handlers_after_start = list(worker_logger.handlers)
        assert svc._log_handler is not None
        assert svc._log_handler in handlers_after_start

        await svc.stop(graceful=False)
        handlers_after_stop = list(worker_logger.handlers)
        assert svc._log_handler is None
        worker_log_handlers = [h for h in handlers_after_stop if isinstance(h, WorkerLogHandler)]
        assert not any(h not in handlers_after_start for h in worker_log_handlers)
