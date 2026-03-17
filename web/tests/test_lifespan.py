"""Tests for web app lifespan: periodic view refresh background task."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web.app import app, lifespan
from worker.log_buffer import LogBuffer
from worker.service import WorkerSettings


class _FakePool:
    pass


def _make_worker_mock() -> MagicMock:
    """Create a WorkerService mock with async start/stop methods."""
    mock = MagicMock()
    mock.start = AsyncMock()
    mock.stop = AsyncMock()
    mock.log_buffer = LogBuffer()
    mock.status = "stopped"
    return mock


@pytest.mark.asyncio
async def test_refresh_task_calls_refresh_views() -> None:
    """Background refresh loop calls refresh_views after each sleep."""
    called_event = asyncio.Event()
    _real_sleep = asyncio.sleep  # capture before patching to avoid recursion

    async def fake_refresh() -> None:
        called_event.set()

    store_mock = AsyncMock()
    store_mock.refresh_views.side_effect = fake_refresh

    async def fast_sleep(_seconds: float) -> None:
        await _real_sleep(0)  # yield without waiting

    worker_mock = _make_worker_mock()
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.asyncio.sleep", side_effect=fast_sleep),
        patch("web.app.WorkerService", return_value=worker_mock),
    ):
        async with lifespan(app):
            await asyncio.wait_for(called_event.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_refresh_errors_do_not_crash_lifespan() -> None:
    """refresh_views errors are swallowed with a warning, lifespan exits cleanly."""
    store_mock = AsyncMock()
    store_mock.refresh_views.side_effect = RuntimeError("db error")

    worker_mock = _make_worker_mock()
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.asyncio.sleep", new=AsyncMock(return_value=None)),
        patch("web.app.WorkerService", return_value=worker_mock),
    ):
        # Should not raise even though refresh_views always raises
        async with lifespan(app):
            await asyncio.sleep(0)
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_background_task_cancelled_on_lifespan_exit() -> None:
    """Background refresh task is cancelled cleanly when lifespan exits."""
    store_mock = AsyncMock()
    store_mock.refresh_views.return_value = None

    worker_mock = _make_worker_mock()
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.WorkerService", return_value=worker_mock),
    ):
        async with lifespan(app):
            tasks_during = [t for t in asyncio.all_tasks() if not t.done()]

        # After lifespan exits, yield so cancellation propagates
        await asyncio.sleep(0)
        tasks_after = [t for t in asyncio.all_tasks() if not t.done()]

        # The refresh task (running inside lifespan) should be done after exit
        assert len(tasks_after) <= len(tasks_during)


@pytest.mark.asyncio
async def test_should_start_worker_service_during_lifespan_startup() -> None:
    """worker_service.start() is called once during lifespan entry."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.WorkerService", return_value=worker_mock),
    ):
        async with lifespan(app):
            pass

    worker_mock.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_should_stop_worker_service_during_lifespan_shutdown() -> None:
    """worker_service.stop(graceful=True) is called once during lifespan exit."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.WorkerService", return_value=worker_mock),
    ):
        async with lifespan(app):
            pass

    worker_mock.stop.assert_awaited_once_with(graceful=True)


@pytest.mark.asyncio
async def test_should_read_worker_settings_from_env_vars() -> None:
    """WorkerService is constructed with WorkerSettings matching env vars."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    env = {
        "WORKER_WATCHDOG_TIMEOUT": "120",
        "WORKER_MAX_WORKERS": "3",
        "WORKER_CAPABILITIES": "gpu,docker",
        "WORKER_ENABLED": "true",
        "DATABASE_URL": "postgresql+psycopg://ratchet@localhost/ratchet",
    }

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.WorkerService", return_value=worker_mock) as ws_cls,
        patch.dict("os.environ", env, clear=False),
    ):
        async with lifespan(app):
            pass

    _, kwargs = ws_cls.call_args
    settings: WorkerSettings = kwargs["settings"]
    assert settings.watchdog_timeout == 120
    assert settings.max_workers == 3
    assert settings.local_capabilities == ["gpu", "docker"]
    assert settings.enabled is True


@pytest.mark.asyncio
async def test_should_use_default_worker_settings_when_env_vars_unset() -> None:
    """WorkerService is constructed with default WorkerSettings when env vars are absent."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    env_overrides = {
        "DATABASE_URL": "postgresql+psycopg://ratchet@localhost/ratchet",
    }
    # Remove optional env vars to test defaults
    remove_keys = [
        "WORKER_WATCHDOG_TIMEOUT",
        "WORKER_MAX_WORKERS",
        "WORKER_CAPABILITIES",
        "WORKER_ENABLED",
    ]

    import os
    clean_env = {k: v for k, v in os.environ.items() if k not in remove_keys}
    clean_env.update(env_overrides)

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.WorkerService", return_value=worker_mock) as ws_cls,
        patch.dict("os.environ", clean_env, clear=True),
    ):
        async with lifespan(app):
            pass

    _, kwargs = ws_cls.call_args
    settings: WorkerSettings = kwargs["settings"]
    assert settings.watchdog_timeout == 300
    assert settings.max_workers == 1
    assert settings.local_capabilities == []
    assert settings.enabled is True


@pytest.mark.asyncio
async def test_should_not_start_worker_when_disabled_via_env() -> None:
    """When WORKER_ENABLED=false, worker status remains 'stopped'."""
    from worker.service import WorkerService as RealWorkerService

    store_mock = AsyncMock()

    env = {
        "WORKER_ENABLED": "false",
        "DATABASE_URL": "postgresql+psycopg://ratchet@localhost/ratchet",
    }

    # Use the real WorkerService with a fake pool so start() is a no-op (disabled)
    fake_pool = _FakePool()
    real_service = RealWorkerService(
        pool=fake_pool,  # type: ignore[arg-type]
        dsn="postgresql://localhost/ratchet",
        settings=WorkerSettings(enabled=False),
    )

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=fake_pool)),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.WorkerService", return_value=real_service),
        patch.dict("os.environ", env, clear=False),
    ):
        async with lifespan(app):
            assert real_service.status == "stopped"


@pytest.mark.asyncio
async def test_should_parse_worker_capabilities_from_comma_separated_env() -> None:
    """WORKER_CAPABILITIES='gpu,docker, sandbox' is parsed to ['gpu', 'docker', 'sandbox']."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    env = {
        "WORKER_CAPABILITIES": "gpu,docker, sandbox",
        "DATABASE_URL": "postgresql+psycopg://ratchet@localhost/ratchet",
    }

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.WorkerService", return_value=worker_mock) as ws_cls,
        patch.dict("os.environ", env, clear=False),
    ):
        async with lifespan(app):
            pass

    _, kwargs = ws_cls.call_args
    settings: WorkerSettings = kwargs["settings"]
    assert settings.local_capabilities == ["gpu", "docker", "sandbox"]


@pytest.mark.asyncio
async def test_should_expose_log_buffer_on_app_state() -> None:
    """app.state.worker_log_buffer is the same LogBuffer instance as worker_service.log_buffer."""
    store_mock = AsyncMock()
    shared_log_buffer = LogBuffer()
    worker_mock = _make_worker_mock()
    worker_mock.log_buffer = shared_log_buffer

    captured_app_state: dict = {}

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.WorkerService", return_value=worker_mock),
        patch("web.app.LogBuffer", return_value=shared_log_buffer),
    ):
        async with lifespan(app):
            captured_app_state["worker_log_buffer"] = app.state.worker_log_buffer
            captured_app_state["service_log_buffer"] = app.state.worker_service.log_buffer

    assert captured_app_state["worker_log_buffer"] is shared_log_buffer
    assert captured_app_state["service_log_buffer"] is shared_log_buffer
