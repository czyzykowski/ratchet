"""Unit tests for get_tasks_for_project and get_features_for_project."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from web.queries import get_features_for_project, get_tasks_for_project


def _make_mock_conn(rows: list) -> MagicMock:
    cur = AsyncMock()
    cur.execute = AsyncMock()
    cur.fetchall = AsyncMock(return_value=rows)
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=None)
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    return conn


@pytest.mark.asyncio
async def test_get_tasks_for_project_returns_title_status_feature_title() -> None:
    project_id = uuid4()
    rows = [
        ("Task A", "ready_for_spec", None),
        ("Task B", "in_progress", "Feature X"),
    ]
    conn = _make_mock_conn(rows)

    result = await get_tasks_for_project(conn, project_id)

    assert len(result) == 2
    assert result[0] == {"title": "Task A", "status": "ready_for_spec", "feature_title": None}
    assert result[1] == {"title": "Task B", "status": "in_progress", "feature_title": "Feature X"}


@pytest.mark.asyncio
async def test_get_tasks_for_project_returns_empty_list_when_no_tasks() -> None:
    project_id = uuid4()
    conn = _make_mock_conn([])

    result = await get_tasks_for_project(conn, project_id)

    assert result == []


@pytest.mark.asyncio
async def test_get_tasks_for_project_passes_project_id_to_query() -> None:
    project_id = uuid4()
    cur = AsyncMock()
    cur.execute = AsyncMock()
    cur.fetchall = AsyncMock(return_value=[])
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=None)
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)

    await get_tasks_for_project(conn, project_id)

    cur.execute.assert_called_once()
    call_args = cur.execute.call_args
    assert str(project_id) in call_args[0][1]


@pytest.mark.asyncio
async def test_get_features_for_project_returns_title_description_counts() -> None:
    project_id = uuid4()
    rows = [
        ("Auth", "Login flows", 3, 2, False),
        ("Dashboard", "UI features", 1, 0, False),
    ]
    conn = _make_mock_conn(rows)

    result = await get_features_for_project(conn, project_id)

    assert len(result) == 2
    assert result[0] == {
        "title": "Auth",
        "description": "Login flows",
        "spec_count": 3,
        "compiled_count": 2,
        "abandoned": False,
    }
    assert result[1] == {
        "title": "Dashboard",
        "description": "UI features",
        "spec_count": 1,
        "compiled_count": 0,
        "abandoned": False,
    }


@pytest.mark.asyncio
async def test_get_features_for_project_returns_empty_list_when_no_features() -> None:
    project_id = uuid4()
    conn = _make_mock_conn([])

    result = await get_features_for_project(conn, project_id)

    assert result == []


@pytest.mark.asyncio
async def test_get_features_for_project_passes_project_id_to_query() -> None:
    project_id = uuid4()
    cur = AsyncMock()
    cur.execute = AsyncMock()
    cur.fetchall = AsyncMock(return_value=[])
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=None)
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)

    await get_features_for_project(conn, project_id)

    cur.execute.assert_called_once()
    call_args = cur.execute.call_args
    assert str(project_id) in call_args[0][1]
