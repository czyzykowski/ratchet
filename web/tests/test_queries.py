"""Unit tests for web/queries.py helper functions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from web.queries import get_task_qa_failure_reason


def _make_conn(fetchone_return: Any) -> Any:
    """Build a minimal async mock connection whose cursor().fetchone() returns the given value."""
    cursor = AsyncMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=fetchone_return)

    conn = MagicMock()

    @asynccontextmanager
    async def _cursor_ctx():  # type: ignore[return]
        yield cursor

    conn.cursor = _cursor_ctx
    return conn


@pytest.mark.asyncio
async def test_should_return_none_when_no_blocked_event_exists() -> None:
    task_id = uuid4()
    conn = _make_conn(fetchone_return=None)
    result = await get_task_qa_failure_reason(conn, task_id)
    assert result is None


@pytest.mark.asyncio
async def test_should_return_failure_reason_when_blocked_event_has_failure_reason() -> None:
    task_id = uuid4()
    conn = _make_conn(fetchone_return=("QA step 'test' failed: 3 assertions failed",))
    result = await get_task_qa_failure_reason(conn, task_id)
    assert result == "QA step 'test' failed: 3 assertions failed"


@pytest.mark.asyncio
async def test_should_return_none_when_blocked_event_has_no_failure_reason() -> None:
    task_id = uuid4()
    conn = _make_conn(fetchone_return=(None,))
    result = await get_task_qa_failure_reason(conn, task_id)
    assert result is None
