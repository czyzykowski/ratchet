"""Unit tests for WorkerService lifecycle management."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker.service import WorkerService, WorkerSettings


def _make_service(settings: WorkerSettings | None = None) -> WorkerService:
    pool = MagicMock()
    return WorkerService(pool=pool, dsn="postgresql://localhost/test", settings=settings)


def _blocking_loop_factory() -> tuple[asyncio.Future, AsyncMock]:
    """Return a Future and an async mock that blocks until the future resolves."""
    fut: asyncio.Future = asyncio.Future()

    async def _loop(*args, **kwargs):
        await fut

    mock = AsyncMock(side_effect=_loop)
    return fut, mock


def _error_loop_factory(exc: Exception) -> AsyncMock:
    async def _loop(*args, **kwargs):
        raise exc

    return AsyncMock(side_effect=_loop)


@pytest.fixture(autouse=True)
def mock_postgres_store():
    with patch("worker.service.PostgresStore") as m:
        yield m


@pytest.fixture(autouse=True)
def mock_invoker():
    with patch("worker.service.ClaudeCodeInvoker") as m:
        instance = MagicMock()
        instance.terminate = MagicMock()
        m.return_value = instance
        yield m, instance


def test_initial_status_is_stopped():
    svc = _make_service()
    assert svc.status == "stopped"
    assert svc.started_at is None
    assert svc.error_message is None


@pytest.mark.asyncio
async def test_start_sets_running(mock_invoker):
    fut, loop_mock = _blocking_loop_factory()
    with patch("worker.service.notification_loop", loop_mock):
        svc = _make_service()
        await svc.start()
        assert svc.status == "running"
        assert svc.started_at is not None
        # cleanup
        await svc.stop(graceful=False)


@pytest.mark.asyncio
async def test_start_disabled_stays_stopped():
    svc = _make_service(settings=WorkerSettings(enabled=False))
    await svc.start()
    assert svc.status == "stopped"
    assert svc.started_at is None


@pytest.mark.asyncio
async def test_stop_after_start(mock_invoker):
    fut, loop_mock = _blocking_loop_factory()
    with patch("worker.service.notification_loop", loop_mock):
        svc = _make_service()
        await svc.start()
        await svc.stop()
        assert svc.status == "stopped"
        assert svc.started_at is None


@pytest.mark.asyncio
async def test_stop_graceful_calls_terminate(mock_invoker):
    invoker_cls, invoker_instance = mock_invoker
    fut, loop_mock = _blocking_loop_factory()
    with patch("worker.service.notification_loop", loop_mock):
        svc = _make_service()
        await svc.start()
        await svc.stop(graceful=True)
        invoker_instance.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_stop_not_graceful_skips_terminate(mock_invoker):
    invoker_cls, invoker_instance = mock_invoker
    fut, loop_mock = _blocking_loop_factory()
    with patch("worker.service.notification_loop", loop_mock):
        svc = _make_service()
        await svc.start()
        await svc.stop(graceful=False)
        invoker_instance.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_restart_cycle(mock_invoker):
    fut, loop_mock = _blocking_loop_factory()
    with patch("worker.service.notification_loop", loop_mock):
        svc = _make_service()
        await svc.start()
        await svc.restart()
        assert svc.status == "running"
        await svc.stop(graceful=False)


@pytest.mark.asyncio
async def test_update_settings_triggers_restart_when_running(mock_invoker):
    fut, loop_mock = _blocking_loop_factory()
    with patch("worker.service.notification_loop", loop_mock):
        svc = _make_service()
        await svc.start()
        new_settings = WorkerSettings(max_workers=2)
        await svc.update_settings(new_settings)
        assert svc.status == "running"
        assert svc.settings.max_workers == 2
        await svc.stop(graceful=False)


@pytest.mark.asyncio
async def test_update_settings_no_restart_when_stopped():
    svc = _make_service()
    new_settings = WorkerSettings(max_workers=3)
    await svc.update_settings(new_settings)
    assert svc.status == "stopped"
    assert svc.settings.max_workers == 3


@pytest.mark.asyncio
async def test_loop_exception_sets_error(mock_invoker):
    error_mock = _error_loop_factory(RuntimeError("boom"))
    with patch("worker.service.notification_loop", error_mock):
        svc = _make_service()
        await svc.start()
        # Give the event loop a moment to run the task and hit the exception
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert svc.status == "error"
        assert svc.error_message == "boom"


@pytest.mark.asyncio
async def test_stop_when_already_stopped_is_noop():
    svc = _make_service()
    assert svc.status == "stopped"
    # Should not raise
    await svc.stop()
    assert svc.status == "stopped"
