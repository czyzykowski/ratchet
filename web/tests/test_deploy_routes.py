"""Unit tests for GET/POST /tasks/{id}/deploy."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from core import events as ev
from core.store import InMemoryStore
from web.routes.tasks import router

_PROJECTS_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


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


async def _setup_task(
    store: InMemoryStore,
    task_id: UUID,
    project_id: UUID,
    title: str,
    status: str,
    branch_name: str | None = None,
) -> None:
    """Set up a task in the store with the given status."""
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": title,
            "status": ev.READY_FOR_SPEC,
        },
    )
    if status != ev.READY_FOR_SPEC:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={"from_status": ev.READY_FOR_SPEC, "to_status": status},
        )
    if branch_name:
        execution_id = uuid4()
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_executions",
            event_type=ev.EXECUTION_STARTED,
            payload={
                "execution_id": str(execution_id),
                "branch_name": branch_name,
            },
        )


def _fake_task(task_id: UUID, project_id: UUID, title: str, status: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": task_id,
        "project_id": project_id,
        "title": title,
        "status": status,
        "current_spec_id": None,
        "refinement_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def _fake_project(project_id: UUID, local_path: str = "/tmp/repo") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": project_id,
        "name": "My Project",
        "repo_url": local_path,
        "local_path": local_path,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.asyncio
async def test_deploy_confirm_wrong_status(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(store, task_id, project_id, "My Task", ev.IN_PROGRESS)

    with patch("web.routes.tasks.PostgresStore") as mock_store_cls:
        mock_store_cls.return_value = store
        response = client.get(f"/tasks/{task_id}/deploy")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deploy_skip_merge(store: InMemoryStore, client: TestClient) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )

    fake_task = _fake_task(task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT)
    fake_proj = _fake_project(project_id)

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with (
        patch("web.routes.tasks.PostgresStore") as mock_store_cls,
        patch("web.routes.tasks.queries.get_task", new_callable=AsyncMock, return_value=fake_task),
        patch(
            "web.routes.tasks.queries.get_project", new_callable=AsyncMock, return_value=fake_proj
        ),
        patch("web.routes.tasks.subprocess.run") as mock_subprocess,
    ):
        mock_store_cls.return_value = store
        client.app.state.pool = mock_pool  # type: ignore[union-attr]
        response = client.post(
            f"/tasks/{task_id}/deploy",
            data={"target_branch": "develop", "skip_merge": "on"},
        )

    # Assert no subprocess calls were made
    mock_subprocess.assert_not_called()

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    session = client.get("/_session").json()
    assert "Deployed" in session.get("flash", "")


@pytest.mark.asyncio
async def test_deploy_git_failure(store: InMemoryStore, client: TestClient) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )

    fake_task = _fake_task(task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT)
    fake_proj = _fake_project(project_id)

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    git_error = subprocess.CalledProcessError(1, ["git", "checkout"], stderr=b"branch not found")

    with (
        patch("web.routes.tasks.PostgresStore") as mock_store_cls,
        patch("web.routes.tasks.queries.get_task", new_callable=AsyncMock, return_value=fake_task),
        patch(
            "web.routes.tasks.queries.get_project", new_callable=AsyncMock, return_value=fake_proj
        ),
        patch("web.routes.tasks.subprocess.run", side_effect=git_error),
    ):
        mock_store_cls.return_value = store
        client.app.state.pool = mock_pool  # type: ignore[union-attr]
        response = client.post(
            f"/tasks/{task_id}/deploy",
            data={"target_branch": "develop"},
        )

    assert response.status_code == 400
    assert "branch not found" in response.text
