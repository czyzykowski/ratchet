"""Tests for LocalWorkerManager lifecycle."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.store import InMemoryStore
from web.local_worker import LocalWorkerManager, LocalWorkerSettings
from worker.log_buffer import LogBuffer


def _make_fake_process(
    pid: int = 12345,
    returncode: int | None = None,
) -> MagicMock:
    """Create a mock asyncio subprocess.Process."""
    process = MagicMock()
    process.pid = pid
    process.returncode = returncode
    process.stdout = AsyncMock()
    process.stderr = AsyncMock()
    # wait() is a coroutine that returns returncode
    process.wait = AsyncMock(return_value=0)
    process.send_signal = MagicMock()
    process.kill = MagicMock()
    return process


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def log_buffer() -> LogBuffer:
    return LogBuffer()


@pytest.fixture
def settings() -> LocalWorkerSettings:
    return LocalWorkerSettings(enabled=True, capabilities=[], port=8000)


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_spawn_subprocess_when_started(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """start() calls asyncio.create_subprocess_exec and sets status to running."""
    fake_process = _make_fake_process()
    fake_process.wait = AsyncMock(return_value=0)

    with patch(
        "web.local_worker.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_process),
    ) as mock_exec:
        manager = LocalWorkerManager(store=store, settings=settings)
        with patch.object(manager, "_pump_stream", new=AsyncMock()):
            with patch.object(manager, "_monitor_process", new=AsyncMock()):
                await manager.start()

    assert manager.status == "running"
    assert manager.pid == 12345
    assert manager.started_at is not None
    mock_exec.assert_awaited_once()
    # Verify --remote flag is in command
    cmd = mock_exec.call_args[0]
    assert "--remote" in cmd
    assert any("ws://localhost:8000/ws/worker" in arg for arg in cmd)

    await manager.stop(graceful=False)


@pytest.mark.asyncio
async def test_should_include_capabilities_in_command_when_set(
    store: InMemoryStore,
) -> None:
    """start() passes --capabilities when capabilities list is non-empty."""
    settings = LocalWorkerSettings(enabled=True, capabilities=["python", "docker"], port=8000)
    fake_process = _make_fake_process()
    fake_process.wait = AsyncMock(return_value=0)

    with patch(
        "web.local_worker.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_process),
    ) as mock_exec:
        manager = LocalWorkerManager(store=store, settings=settings)
        with patch.object(manager, "_pump_stream", new=AsyncMock()):
            with patch.object(manager, "_monitor_process", new=AsyncMock()):
                await manager.start()

    cmd = mock_exec.call_args[0]
    assert "--capabilities" in cmd
    cap_idx = cmd.index("--capabilities")
    assert "python" in cmd[cap_idx + 1]
    assert "docker" in cmd[cap_idx + 1]

    await manager.stop(graceful=False)


@pytest.mark.asyncio
async def test_should_be_noop_when_disabled(store: InMemoryStore) -> None:
    """start() is a no-op when enabled=False."""
    settings = LocalWorkerSettings(enabled=False)
    manager = LocalWorkerManager(store=store, settings=settings)

    with patch(
        "web.local_worker.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as mock_exec:
        await manager.start()

    assert manager.status == "stopped"
    mock_exec.assert_not_awaited()


@pytest.mark.asyncio
async def test_should_raise_when_already_running(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """start() raises RuntimeError when status is already running."""
    fake_process = _make_fake_process()
    fake_process.wait = AsyncMock(return_value=0)

    with patch(
        "web.local_worker.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_process),
    ):
        manager = LocalWorkerManager(store=store, settings=settings)
        with patch.object(manager, "_pump_stream", new=AsyncMock()):
            with patch.object(manager, "_monitor_process", new=AsyncMock()):
                await manager.start()

    with pytest.raises(RuntimeError, match="already running"):
        await manager.start()

    await manager.stop(graceful=False)


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_send_sigterm_when_stopping_gracefully(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """stop(graceful=True) sends SIGTERM to the subprocess."""
    fake_process = _make_fake_process()
    fake_process.wait = AsyncMock(return_value=0)

    with patch(
        "web.local_worker.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_process),
    ):
        manager = LocalWorkerManager(store=store, settings=settings)
        with patch.object(manager, "_pump_stream", new=AsyncMock()):
            with patch.object(manager, "_monitor_process", new=AsyncMock()):
                await manager.start()
        await manager.stop(graceful=True)

    fake_process.send_signal.assert_called_once_with(signal.SIGTERM)
    assert manager.status == "stopped"


@pytest.mark.asyncio
async def test_should_send_sigkill_when_stopping_forcefully(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """stop(graceful=False) sends SIGKILL to the subprocess."""
    fake_process = _make_fake_process()
    fake_process.wait = AsyncMock(return_value=0)

    with patch(
        "web.local_worker.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_process),
    ):
        manager = LocalWorkerManager(store=store, settings=settings)
        with patch.object(manager, "_pump_stream", new=AsyncMock()):
            with patch.object(manager, "_monitor_process", new=AsyncMock()):
                await manager.start()
        await manager.stop(graceful=False)

    fake_process.kill.assert_called_once()
    assert manager.status == "stopped"


@pytest.mark.asyncio
async def test_should_be_noop_when_stopping_already_stopped(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """stop() is a no-op when already stopped."""
    manager = LocalWorkerManager(store=store, settings=settings)
    # Should not raise
    await manager.stop(graceful=True)
    assert manager.status == "stopped"


# ---------------------------------------------------------------------------
# restart()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_call_stop_then_start_on_restart(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """restart() stops then starts the worker."""
    call_order: list[str] = []

    manager = LocalWorkerManager(store=store, settings=settings)

    async def _fake_stop(graceful: bool = True) -> None:
        call_order.append("stop")

    async def _fake_start() -> None:
        call_order.append("start")

    manager.stop = _fake_stop  # type: ignore[method-assign]
    manager.start = _fake_start  # type: ignore[method-assign]

    await manager.restart(graceful=True)

    assert call_order == ["stop", "start"]


# ---------------------------------------------------------------------------
# Crash detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_set_error_status_on_unexpected_exit(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """_monitor_process sets status to error when subprocess exits unexpectedly."""
    fake_process = _make_fake_process(returncode=None)
    # Simulate process exit with returncode=1
    fake_process.wait = AsyncMock(return_value=1)

    with patch(
        "web.local_worker.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_process),
    ):
        manager = LocalWorkerManager(store=store, settings=settings)
        with patch.object(manager, "_pump_stream", new=AsyncMock()):
            await manager.start()
        # Let the monitor task run
        await asyncio.sleep(0.05)

    assert manager.status == "error"
    assert manager.error_message is not None
    assert "1" in manager.error_message


# ---------------------------------------------------------------------------
# update_settings()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_restart_on_settings_update_when_running(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """update_settings() restarts the worker if currently running."""
    manager = LocalWorkerManager(store=store, settings=settings)
    manager._status = "running"

    restarted = False

    async def _fake_restart(graceful: bool = True) -> None:
        nonlocal restarted
        restarted = True

    manager.restart = _fake_restart  # type: ignore[method-assign]

    new_settings = LocalWorkerSettings(enabled=True, capabilities=["python"], port=8001)
    await manager.update_settings(new_settings)

    assert manager.settings.capabilities == ["python"]
    assert manager.settings.port == 8001
    assert restarted


@pytest.mark.asyncio
async def test_should_not_restart_on_settings_update_when_stopped(
    store: InMemoryStore, settings: LocalWorkerSettings
) -> None:
    """update_settings() does not restart the worker when stopped."""
    manager = LocalWorkerManager(store=store, settings=settings)

    restarted = False

    async def _fake_restart(graceful: bool = True) -> None:
        nonlocal restarted
        restarted = True

    manager.restart = _fake_restart  # type: ignore[method-assign]

    new_settings = LocalWorkerSettings(enabled=False)
    await manager.update_settings(new_settings)

    assert not restarted
    assert manager.settings.enabled is False
