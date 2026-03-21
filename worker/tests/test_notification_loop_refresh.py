"""Tests for store.refresh_views() calls in notification_loop."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import events as ev
from core.invoker import InvocationResult
from core.store import InMemoryStore
from worker.dispatcher import DispatchResult, ProjectDispatcher
from worker.runner import notification_loop


def _make_invoker() -> MagicMock:
    eid = uuid.uuid4()
    invoker = MagicMock()
    invoker.invoke.return_value = InvocationResult(
        execution_id=eid,
        status="completed",
        failure_reason=None,
        trace_id=eid,
    )
    return invoker


class _MockNotificationListener:
    """Mock NotificationListener that yields controlled notifications then stops."""

    def __init__(self, notifications: list[tuple[str, ...]]) -> None:
        self._notifications = notifications

    async def __aenter__(self) -> _MockNotificationListener:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def listen(self) -> AsyncGenerator[tuple[str, ...], None]:
        for item in self._notifications:
            yield item


def _idle_result():
    return DispatchResult(action="idle", success=True)


@pytest.mark.asyncio
async def test_refresh_called_after_startup_catchup() -> None:
    """store.refresh_views() is called after startup catchup completes."""
    store = InMemoryStore()
    store.refresh_views = AsyncMock()  # type: ignore[method-assign]
    invoker = _make_invoker()

    mock_listener = _MockNotificationListener([])  # no notifications

    with (
        patch.object(ProjectDispatcher, "dispatch", new=AsyncMock(return_value=_idle_result())),
        patch.object(ProjectDispatcher, "compile_once", new=AsyncMock(return_value=_idle_result())),
        patch.object(ProjectDispatcher, "recover_orphans", new=AsyncMock(return_value=0)),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(store, invoker, dsn="postgresql://fake/test")

    store.refresh_views.assert_called()


@pytest.mark.asyncio
async def test_refresh_called_after_task_notification() -> None:
    """store.refresh_views() is called after a task notification is processed."""
    store = InMemoryStore()
    store.refresh_views = AsyncMock()  # type: ignore[method-assign]
    invoker = _make_invoker()

    task_id = str(uuid.uuid4())
    notifications: list[tuple[str, ...]] = [
        ("task", task_id, ev.READY_FOR_IMPLEMENTATION),
    ]
    mock_listener = _MockNotificationListener(notifications)

    with (
        patch.object(ProjectDispatcher, "dispatch", new=AsyncMock(return_value=_idle_result())),
        patch.object(ProjectDispatcher, "compile_once", new=AsyncMock(return_value=_idle_result())),
        patch.object(ProjectDispatcher, "recover_orphans", new=AsyncMock(return_value=0)),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(store, invoker, dsn="postgresql://fake/test")

    # Called at least twice: once after catchup, once after the notification
    assert store.refresh_views.call_count >= 2


@pytest.mark.asyncio
async def test_refresh_called_after_compile_notification() -> None:
    """store.refresh_views() is called after a compile notification is processed."""
    store = InMemoryStore()
    store.refresh_views = AsyncMock()  # type: ignore[method-assign]
    invoker = _make_invoker()

    notifications: list[tuple[str, ...]] = [
        ("compile", "new_spec"),
    ]
    mock_listener = _MockNotificationListener(notifications)

    with (
        patch.object(ProjectDispatcher, "dispatch", new=AsyncMock(return_value=_idle_result())),
        patch.object(ProjectDispatcher, "compile_once", new=AsyncMock(return_value=_idle_result())),
        patch.object(ProjectDispatcher, "recover_orphans", new=AsyncMock(return_value=0)),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(store, invoker, dsn="postgresql://fake/test")

    # Called at least twice: once after catchup, once after the compile notification
    assert store.refresh_views.call_count >= 2


@pytest.mark.asyncio
async def test_refresh_errors_do_not_crash_notification_loop() -> None:
    """refresh_views errors are swallowed with a warning; the loop continues."""
    store = InMemoryStore()
    store.refresh_views = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("db connection lost")
    )
    invoker = _make_invoker()

    task_id = str(uuid.uuid4())
    notifications: list[tuple[str, ...]] = [
        ("task", task_id, ev.READY_FOR_IMPLEMENTATION),
    ]
    mock_listener = _MockNotificationListener(notifications)

    with (
        patch.object(ProjectDispatcher, "dispatch", new=AsyncMock(return_value=_idle_result())),
        patch.object(ProjectDispatcher, "compile_once", new=AsyncMock(return_value=_idle_result())),
        patch.object(ProjectDispatcher, "recover_orphans", new=AsyncMock(return_value=0)),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        # Should not raise even though refresh_views always raises
        await notification_loop(store, invoker, dsn="postgresql://fake/test")
