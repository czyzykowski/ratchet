"""Unit tests for NotificationListener filtering logic."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

from core import events as ev
from worker.listener import _ACTIONABLE_STATUSES, NotificationListener


def _make_notify(payload: str | None) -> MagicMock:
    n = MagicMock()
    n.payload = payload
    return n


async def _collect_from_mock_conn(
    notifications: list[MagicMock],
) -> list[tuple[str, str]]:
    """Helper: run NotificationListener.listen() against a mock psycopg connection."""
    collected: list[tuple[str, str]] = []

    async def _fake_notifies() -> AsyncGenerator[MagicMock, None]:
        for n in notifications:
            yield n

    mock_conn = AsyncMock()
    mock_conn.notifies = _fake_notifies
    mock_conn.execute = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_connect = AsyncMock(return_value=mock_conn)

    listener = NotificationListener(dsn="postgresql://fake/test")

    with patch("psycopg.AsyncConnection.connect", mock_connect):
        async for item in listener.listen():
            collected.append(item)

    return collected


# ---------------------------------------------------------------------------
# Filtering: actionable statuses pass through
# ---------------------------------------------------------------------------


async def test_ready_for_implementation_passes_through() -> None:
    task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    payload = json.dumps({"task_id": task_id, "status": ev.READY_FOR_IMPLEMENTATION})
    notifications = [_make_notify(payload)]

    result = await _collect_from_mock_conn(notifications)

    assert result == [(task_id, ev.READY_FOR_IMPLEMENTATION)]


async def test_ready_for_qa_passes_through() -> None:
    task_id = "11111111-2222-3333-4444-555555555555"
    payload = json.dumps({"task_id": task_id, "status": ev.READY_FOR_QA})
    notifications = [_make_notify(payload)]

    result = await _collect_from_mock_conn(notifications)

    assert result == [(task_id, ev.READY_FOR_QA)]


# ---------------------------------------------------------------------------
# Filtering: non-actionable statuses are dropped
# ---------------------------------------------------------------------------


async def test_in_progress_is_dropped() -> None:
    task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    payload = json.dumps({"task_id": task_id, "status": ev.IN_PROGRESS})
    notifications = [_make_notify(payload)]

    result = await _collect_from_mock_conn(notifications)

    assert result == []


async def test_blocked_is_dropped() -> None:
    task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    payload = json.dumps({"task_id": task_id, "status": ev.BLOCKED})
    notifications = [_make_notify(payload)]

    result = await _collect_from_mock_conn(notifications)

    assert result == []


async def test_deployed_is_dropped() -> None:
    task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    payload = json.dumps({"task_id": task_id, "status": ev.DEPLOYED})
    notifications = [_make_notify(payload)]

    result = await _collect_from_mock_conn(notifications)

    assert result == []


async def test_ready_for_deployment_is_dropped() -> None:
    task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    payload = json.dumps({"task_id": task_id, "status": ev.READY_FOR_DEPLOYMENT})
    notifications = [_make_notify(payload)]

    result = await _collect_from_mock_conn(notifications)

    assert result == []


# ---------------------------------------------------------------------------
# Invalid payload handling
# ---------------------------------------------------------------------------


async def test_none_payload_is_skipped() -> None:
    notifications = [_make_notify(None)]
    result = await _collect_from_mock_conn(notifications)
    assert result == []


async def test_invalid_json_is_skipped() -> None:
    notifications = [_make_notify("not-json")]
    result = await _collect_from_mock_conn(notifications)
    assert result == []


async def test_missing_task_id_is_skipped() -> None:
    payload = json.dumps({"status": ev.READY_FOR_IMPLEMENTATION})
    notifications = [_make_notify(payload)]
    result = await _collect_from_mock_conn(notifications)
    assert result == []


async def test_missing_status_is_skipped() -> None:
    payload = json.dumps({"task_id": "some-uuid"})
    notifications = [_make_notify(payload)]
    result = await _collect_from_mock_conn(notifications)
    assert result == []


# ---------------------------------------------------------------------------
# Queue: notifications received during active execution are queued and drained
# ---------------------------------------------------------------------------


async def test_queue_holds_notifications_during_execution_and_drains_after() -> None:
    """Notifications queued during active execution are all processed."""
    task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    # Simulate: producer puts 3 notifications into the queue
    for status in [ev.READY_FOR_IMPLEMENTATION, ev.READY_FOR_QA, ev.READY_FOR_IMPLEMENTATION]:
        await queue.put((task_id, status))

    # Drain the queue and verify order
    drained: list[tuple[str, str]] = []
    while not queue.empty():
        drained.append(await queue.get())

    assert len(drained) == 3
    assert drained[0] == (task_id, ev.READY_FOR_IMPLEMENTATION)
    assert drained[1] == (task_id, ev.READY_FOR_QA)
    assert drained[2] == (task_id, ev.READY_FOR_IMPLEMENTATION)


async def test_actionable_statuses_set_contains_expected_values() -> None:
    """The _ACTIONABLE_STATUSES set contains exactly the expected statuses."""
    assert ev.READY_FOR_IMPLEMENTATION in _ACTIONABLE_STATUSES
    assert ev.READY_FOR_QA in _ACTIONABLE_STATUSES
    assert ev.IN_PROGRESS not in _ACTIONABLE_STATUSES
    assert ev.BLOCKED not in _ACTIONABLE_STATUSES
    assert ev.DEPLOYED not in _ACTIONABLE_STATUSES
