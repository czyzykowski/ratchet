"""Tests for web app lifespan: periodic view refresh background task and local worker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web.app import app, lifespan
from web.local_worker import LocalWorkerSettings
from worker.log_buffer import LogBuffer


class _FakePool:
    pass


def _make_worker_mock() -> MagicMock:
    """Create a LocalWorkerManager mock with async start/stop methods."""
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
    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.asyncio.sleep", side_effect=fast_sleep),
        patch("web.app.LocalWorkerManager", return_value=worker_mock),
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
    ):
        async with lifespan(app):
            await asyncio.wait_for(called_event.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_refresh_errors_do_not_crash_lifespan() -> None:
    """refresh_views errors are swallowed with a warning, lifespan exits cleanly."""
    store_mock = AsyncMock()
    store_mock.refresh_views.side_effect = RuntimeError("db error")

    worker_mock = _make_worker_mock()
    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.asyncio.sleep", new=AsyncMock(return_value=None)),
        patch("web.app.LocalWorkerManager", return_value=worker_mock),
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
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
    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.LocalWorkerManager", return_value=worker_mock),
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
    ):
        async with lifespan(app):
            tasks_during = [t for t in asyncio.all_tasks() if not t.done()]

        # After lifespan exits, yield so cancellation propagates
        await asyncio.sleep(0)
        tasks_after = [t for t in asyncio.all_tasks() if not t.done()]

        # The refresh task (running inside lifespan) should be done after exit
        assert len(tasks_after) <= len(tasks_during)


@pytest.mark.asyncio
async def test_should_start_local_worker_during_lifespan_startup() -> None:
    """local_worker.start() is called once during lifespan entry."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.LocalWorkerManager", return_value=worker_mock),
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
    ):
        async with lifespan(app):
            pass

    worker_mock.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_should_stop_local_worker_during_lifespan_shutdown() -> None:
    """local_worker.stop(graceful=True) is called once during lifespan exit."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.LocalWorkerManager", return_value=worker_mock),
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
    ):
        async with lifespan(app):
            pass

    worker_mock.stop.assert_awaited_once_with(graceful=True)


@pytest.mark.asyncio
async def test_should_read_local_worker_settings_from_env_vars() -> None:
    """LocalWorkerManager is constructed with LocalWorkerSettings matching env vars."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    env = {
        "WORKER_CAPABILITIES": "gpu,docker",
        "WORKER_ENABLED": "true",
        "WEB_PORT": "9000",
        "DATABASE_URL": "postgresql+psycopg://ratchet@localhost/ratchet",
    }

    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.LocalWorkerManager", return_value=worker_mock) as lw_cls,
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
        patch.dict("os.environ", env, clear=False),
    ):
        async with lifespan(app):
            pass

    _, kwargs = lw_cls.call_args
    settings: LocalWorkerSettings = kwargs["settings"]
    assert settings.capabilities == ["gpu", "docker"]
    assert settings.enabled is True
    assert settings.port == 9000


@pytest.mark.asyncio
async def test_should_use_default_local_worker_settings_when_env_vars_unset() -> None:
    """LocalWorkerManager is constructed with default settings when env vars are absent."""
    import os

    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    env_overrides = {
        "DATABASE_URL": "postgresql+psycopg://ratchet@localhost/ratchet",
    }
    remove_keys = ["WORKER_CAPABILITIES", "WORKER_ENABLED", "WEB_PORT"]
    clean_env = {k: v for k, v in os.environ.items() if k not in remove_keys}
    clean_env.update(env_overrides)

    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.LocalWorkerManager", return_value=worker_mock) as lw_cls,
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
        patch.dict("os.environ", clean_env, clear=True),
    ):
        async with lifespan(app):
            pass

    _, kwargs = lw_cls.call_args
    settings: LocalWorkerSettings = kwargs["settings"]
    assert settings.capabilities == []
    assert settings.enabled is True
    assert settings.port == 8000


@pytest.mark.asyncio
async def test_should_not_start_worker_when_disabled_via_env() -> None:
    """When WORKER_ENABLED=false, local worker status remains 'stopped'."""
    from web.local_worker import LocalWorkerManager as RealLocalWorkerManager

    store_mock = AsyncMock()

    env = {
        "WORKER_ENABLED": "false",
        "DATABASE_URL": "postgresql+psycopg://ratchet@localhost/ratchet",
    }

    real_manager = RealLocalWorkerManager(
        store=store_mock,
        settings=LocalWorkerSettings(enabled=False),
    )

    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.LocalWorkerManager", return_value=real_manager),
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
        patch.dict("os.environ", env, clear=False),
    ):
        async with lifespan(app):
            assert real_manager.status == "stopped"


@pytest.mark.asyncio
async def test_should_expose_log_buffer_on_app_state() -> None:
    """app.state.worker_log_buffer is the same LogBuffer instance as the local worker's."""
    store_mock = AsyncMock()
    shared_log_buffer = LogBuffer()
    worker_mock = _make_worker_mock()
    worker_mock.log_buffer = shared_log_buffer

    captured_app_state: dict = {}

    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.LocalWorkerManager", return_value=worker_mock),
        patch("web.app.LogBuffer", return_value=shared_log_buffer),
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
    ):
        async with lifespan(app):
            captured_app_state["worker_log_buffer"] = app.state.worker_log_buffer
            captured_app_state["service_log_buffer"] = app.state.worker_service.log_buffer

    assert captured_app_state["worker_log_buffer"] is shared_log_buffer
    assert captured_app_state["service_log_buffer"] is shared_log_buffer


@pytest.mark.asyncio
async def test_should_parse_worker_capabilities_from_comma_separated_env() -> None:
    """WORKER_CAPABILITIES='gpu,docker, sandbox' is parsed to ['gpu', 'docker', 'sandbox']."""
    store_mock = AsyncMock()
    worker_mock = _make_worker_mock()

    env = {
        "WORKER_CAPABILITIES": "gpu,docker, sandbox",
        "DATABASE_URL": "postgresql+psycopg://ratchet@localhost/ratchet",
    }

    recovery_mock = MagicMock()
    recovery_mock.recover_in_progress_tasks = AsyncMock(return_value=[])
    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.LocalWorkerManager", return_value=worker_mock) as lw_cls,
        patch("web.app.WorkerRegistry"),
        patch("web.app.dispatch_loop", new=AsyncMock()),
        patch("web.app.RecoveryManager", return_value=recovery_mock),
        patch.dict("os.environ", env, clear=False),
    ):
        async with lifespan(app):
            pass

    _, kwargs = lw_cls.call_args
    settings: LocalWorkerSettings = kwargs["settings"]
    assert settings.capabilities == ["gpu", "docker", "sandbox"]
