"""Unit tests for GET /projects/new and POST /projects."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from core.project_manager import OnboardingError
from core.store import InMemoryStore
from web.routes.projects import router


def _make_test_app(store: InMemoryStore) -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(SessionMiddleware, secret_key="test-secret")
    test_app.state.store = store
    test_app.state.pool = MagicMock()
    test_app.include_router(router)

    @test_app.get("/_session")
    async def session_peek(request: Request) -> JSONResponse:
        return JSONResponse(dict(request.session))

    return test_app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store), follow_redirects=False)


def test_new_project_form_renders(client: TestClient) -> None:
    response = client.get("/projects/new")
    assert response.status_code == 200
    assert "Register Project" in response.text


def test_create_project_success(client: TestClient, store: InMemoryStore) -> None:
    with patch(
        "web.routes.projects.ProjectManager.register_project",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.post(
            "/projects",
            data={"name": "My Project", "path": "/tmp/my-repo"},
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    session = client.get("/_session").json()
    assert "My Project" in session.get("flash", "")
    assert "Project registered" in session.get("flash", "")


def test_create_project_onboarding_error(client: TestClient, store: InMemoryStore) -> None:
    with patch(
        "web.routes.projects.ProjectManager.register_project",
        new_callable=AsyncMock,
        side_effect=OnboardingError("not a git repo"),
    ):
        response = client.post(
            "/projects",
            data={"name": "Bad Project", "path": "/tmp/no-such-dir"},
        )

    assert response.status_code == 400
    assert "not a git repo" in response.text


def _make_task(title: str, status: str) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "project_id": uuid4(),
        "title": title,
        "status": status,
        "current_spec_id": None,
        "refinement_count": 0,
        "created_at": None,
        "updated_at": None,
    }


def test_new_task_form_excludes_terminal_statuses(client: TestClient) -> None:
    project_id = uuid4()
    project = MagicMock()
    project.id = project_id
    project.name = "Test Project"

    all_tasks = [
        _make_task("Active Task", "ready_for_spec"),
        _make_task("In Progress Task", "ready_for_implementation"),
        _make_task("Merged Task", "merged"),
        _make_task("Abandoned Task", "abandoned"),
    ]

    mock_conn = MagicMock()

    @asynccontextmanager
    async def _fake_pool_connection() -> AsyncIterator[Any]:
        yield mock_conn

    mock_get_project = patch(
        "web.routes.projects.queries.get_project",
        new_callable=AsyncMock,
        return_value=project,
    )
    mock_get_tasks = patch(
        "web.routes.projects.queries.get_project_tasks",
        new_callable=AsyncMock,
        return_value=all_tasks,
    )
    mock_pool = patch.object(
        client.app.state, "pool", MagicMock(connection=_fake_pool_connection)
    )
    with mock_get_project, mock_get_tasks, mock_pool:
        response = client.get(f"/projects/{project_id}/tasks/new")

    assert response.status_code == 200
    assert "Active Task" in response.text
    assert "In Progress Task" in response.text
    assert "Merged Task" not in response.text
    assert "Abandoned Task" not in response.text
