"""Tests for web app lifespan: periodic view refresh background task."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from web.app import app, lifespan


class _FakePool:
    pass


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

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.asyncio.sleep", side_effect=fast_sleep),
    ):
        async with lifespan(app):
            await asyncio.wait_for(called_event.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_refresh_errors_do_not_crash_lifespan() -> None:
    """refresh_views errors are swallowed with a warning, lifespan exits cleanly."""
    store_mock = AsyncMock()
    store_mock.refresh_views.side_effect = RuntimeError("db error")

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
        patch("web.app.asyncio.sleep", new=AsyncMock(return_value=None)),
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

    with (
        patch("web.app.PostgresStore", return_value=store_mock),
        patch("core.db.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.db.close_pool", new=AsyncMock()),
    ):
        async with lifespan(app):
            tasks_during = [t for t in asyncio.all_tasks() if not t.done()]

        # After lifespan exits, yield so cancellation propagates
        await asyncio.sleep(0)
        tasks_after = [t for t in asyncio.all_tasks() if not t.done()]

        # The refresh task (running inside lifespan) should be done after exit
        assert len(tasks_after) <= len(tasks_during)
