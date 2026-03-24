"""Unit tests for web/queries.py helper functions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from web.queries import (
    get_chat_sessions_for_project,
    get_features_summary_for_project,
    get_task_qa_failure_reason,
    get_tasks_summary_for_project,
)


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


def _make_pool(fetchall_return: Any) -> Any:
    """Build a minimal async mock pool whose connection().cursor().fetchall() returns rows."""
    cursor = AsyncMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=fetchall_return)

    conn = MagicMock()

    @asynccontextmanager
    async def _cursor_ctx():  # type: ignore[return]
        yield cursor

    conn.cursor = _cursor_ctx

    pool = MagicMock()

    @asynccontextmanager
    async def _conn_ctx():  # type: ignore[return]
        yield conn

    pool.connection = _conn_ctx
    return pool


_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


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


# --- get_chat_sessions_for_project ---


@pytest.mark.asyncio
async def test_chat_sessions_should_return_empty_list_when_no_rows() -> None:
    project_id = uuid4()
    pool = _make_pool(fetchall_return=[])
    result = await get_chat_sessions_for_project(pool, project_id)
    assert result == []


@pytest.mark.asyncio
async def test_chat_sessions_should_return_summary_objects_when_rows_exist() -> None:
    project_id = uuid4()
    session_id = uuid4()
    pool = _make_pool(fetchall_return=[(session_id, _NOW)])
    result = await get_chat_sessions_for_project(pool, project_id)
    assert len(result) == 1
    assert result[0].id == session_id
    assert result[0].created_at == _NOW


# --- get_tasks_summary_for_project ---


@pytest.mark.asyncio
async def test_tasks_summary_should_return_empty_list_when_no_rows() -> None:
    project_id = uuid4()
    pool = _make_pool(fetchall_return=[])
    result = await get_tasks_summary_for_project(pool, project_id)
    assert result == []


@pytest.mark.asyncio
async def test_tasks_summary_should_return_none_feature_title_when_no_backlink() -> None:
    project_id = uuid4()
    task_id = uuid4()
    pool = _make_pool(fetchall_return=[(task_id, "My task", "ready_for_spec", None)])
    result = await get_tasks_summary_for_project(pool, project_id)
    assert len(result) == 1
    assert result[0].id == task_id
    assert result[0].title == "My task"
    assert result[0].status == "ready_for_spec"
    assert result[0].feature_title is None


@pytest.mark.asyncio
async def test_tasks_summary_should_return_feature_title_when_backlink_exists() -> None:
    project_id = uuid4()
    task_id = uuid4()
    pool = _make_pool(fetchall_return=[(task_id, "My task", "merged", "Auth Feature")])
    result = await get_tasks_summary_for_project(pool, project_id)
    assert result[0].feature_title == "Auth Feature"


# --- get_features_summary_for_project ---


@pytest.mark.asyncio
async def test_features_summary_should_return_empty_list_when_no_rows() -> None:
    project_id = uuid4()
    pool = _make_pool(fetchall_return=[])
    result = await get_features_summary_for_project(pool, project_id)
    assert result == []


@pytest.mark.asyncio
async def test_features_summary_should_return_correct_counts() -> None:
    project_id = uuid4()
    feature_id = uuid4()
    pool = _make_pool(fetchall_return=[(feature_id, "Auth", 5, 3)])
    result = await get_features_summary_for_project(pool, project_id)
    assert len(result) == 1
    assert result[0].id == feature_id
    assert result[0].title == "Auth"
    assert result[0].spec_count == 5
    assert result[0].compiled_count == 3
