"""Unit tests for POST /api/tasks/{id}/deploy."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.api.tasks import router

_PROJECTS_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    test_app = FastAPI()
    test_app.state.store = store
    test_app.state.pool = MagicMock()
    test_app.state.sse_clients = []
    test_app.include_router(router)
    return test_app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


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


async def _seed_project(
    store: InMemoryStore, project_id: UUID, local_path: str = "/tmp/repo"
) -> None:
    payload = {
        "project_id": str(project_id),
        "name": "My Project",
        "repo_url": local_path,
        "local_path": local_path,
        "status": "active",
    }
    await store.append_event(
        aggregate_id=_PROJECTS_REGISTRY_ID,
        aggregate_type="projects",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_deploy_wrong_status_returns_400(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(store, task_id, project_id, "My Task", ev.IN_PROGRESS)

    response = client.post(f"/tasks/{task_id}/deploy", json={"skip_merge": True})

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_deploy_skip_merge_transitions_to_deployed(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )
    await _seed_project(store, project_id)

    response = client.post(f"/tasks/{task_id}/deploy", json={"skip_merge": True})

    assert response.status_code == 200
    data = response.json()
    assert data["task"]["status"] == ev.DEPLOYED


@pytest.mark.asyncio
async def test_deploy_git_failure_returns_400(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )
    await _seed_project(store, project_id)

    git_error = subprocess.CalledProcessError(1, ["git", "checkout"], stderr=b"branch not found")

    with patch("web.routes.api.tasks.subprocess.run", side_effect=git_error):
        response = client.post(f"/tasks/{task_id}/deploy", json={"skip_merge": False})

    assert response.status_code == 400
    assert "branch not found" in response.json()["detail"]
