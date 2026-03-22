"""Unit tests for GET /api/board."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_pool_mock() -> MagicMock:
    """Return a mock pool whose connection() supports async with."""
    mock_conn = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = cm
    return mock_pool


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = _make_pool_mock()
    app.state.sse_clients = []
    app.include_router(api_router)
    return app


def _board_task(
    task_id: UUID,
    project_id: UUID,
    project_name: str = "Test Project",
    title: str = "My Task",
    status: str = ev.READY_FOR_SPEC,
    has_spec: bool = False,
    refinement_count: int = 0,
    depends_on: list | None = None,
    required_capabilities: list | None = None,
    baseline_qa_failure: str | None = None,
) -> dict:
    """Build a board task dict matching what get_board_tasks() returns."""
    return {
        "id": task_id,
        "project_id": project_id,
        "project_name": project_name,
        "title": title,
        "status": status,
        "has_spec": has_spec,
        "refinement_count": refinement_count,
        "updated_at": None,
        "depends_on": depends_on or [],
        "required_capabilities": required_capabilities or [],
        "baseline_qa_failure": baseline_qa_failure,
    }


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


def test_should_return_board_grouped_by_status_when_tasks_exist(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    tasks = [_board_task(task_id, project_id, title="My Task", status=ev.READY_FOR_SPEC)]

    with patch("web.queries.get_board_tasks", new=AsyncMock(return_value=tasks)):
        response = client.get("/api/board")

    assert response.status_code == 200
    data = response.json()

    assert "columns" in data
    columns = {c["status"]: c for c in data["columns"]}

    assert ev.READY_FOR_SPEC in columns
    rfs_col = columns[ev.READY_FOR_SPEC]
    assert rfs_col["label"] == "READY FOR SPEC"
    assert len(rfs_col["tasks"]) == 1
    assert rfs_col["tasks"][0]["title"] == "My Task"
    assert rfs_col["tasks"][0]["project_name"] == "Test Project"
    assert rfs_col["tasks"][0]["unmet_deps"] == []


def test_should_return_empty_groups_when_no_tasks(
    client: TestClient, store: InMemoryStore
) -> None:
    with patch("web.queries.get_board_tasks", new=AsyncMock(return_value=[])):
        response = client.get("/api/board")

    assert response.status_code == 200
    data = response.json()

    assert "columns" in data
    for col in data["columns"]:
        assert col["tasks"] == []


def test_should_return_required_capabilities_when_task_has_capabilities(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    tasks = [
        _board_task(
            task_id, project_id, title="GPU Task",
            status=ev.READY_FOR_SPEC,
            required_capabilities=["gpu", "linux"],
        )
    ]

    with patch("web.queries.get_board_tasks", new=AsyncMock(return_value=tasks)):
        response = client.get("/api/board")

    assert response.status_code == 200
    data = response.json()

    columns = {c["status"]: c for c in data["columns"]}
    board_tasks = columns[ev.READY_FOR_SPEC]["tasks"]
    assert len(board_tasks) == 1
    assert board_tasks[0]["required_capabilities"] == ["gpu", "linux"]


def test_should_return_empty_required_capabilities_when_task_has_none(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    tasks = [_board_task(task_id, project_id, title="Plain Task", status=ev.READY_FOR_SPEC)]

    with patch("web.queries.get_board_tasks", new=AsyncMock(return_value=tasks)):
        response = client.get("/api/board")

    assert response.status_code == 200
    data = response.json()

    columns = {c["status"]: c for c in data["columns"]}
    board_tasks = columns[ev.READY_FOR_SPEC]["tasks"]
    assert len(board_tasks) == 1
    assert board_tasks[0]["required_capabilities"] == []
