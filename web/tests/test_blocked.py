"""Unit tests for GET /blocked using InMemoryStore (no DB required)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.blocked import router

_PROJECTS_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    test_app = FastAPI()
    test_app.state.store = store
    test_app.include_router(router)
    return TestClient(test_app)


async def _make_project(store: InMemoryStore, name: str = "Test Project") -> UUID:
    project_id = uuid4()
    payload = {
        "project_id": str(project_id),
        "name": name,
        "repo_url": "https://example.com/repo.git",
        "local_path": "/tmp/test",
        "status": "active",
    }
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=_PROJECTS_REGISTRY_ID,
        aggregate_type="projects",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )
    return project_id


async def _make_task(
    store: InMemoryStore, project_id: UUID, title: str, status: str
) -> UUID:
    task_id = uuid4()
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload={"task_id": str(task_id)},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"title": title, "status": ev.READY_FOR_SPEC, "project_id": str(project_id)},
    )
    if status != ev.READY_FOR_SPEC:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={"from_status": ev.READY_FOR_SPEC, "to_status": status},
        )
    return task_id


def test_should_return_200_with_no_blocked_tasks_message_when_store_is_empty(
    client: TestClient,
) -> None:
    with patch(
        "web.routes.blocked._find_last_failure_reason",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.get("/blocked")
    assert response.status_code == 200
    assert "No blocked tasks" in response.text


@pytest.mark.asyncio
async def test_should_list_blocked_task_with_title_and_project_name(
    store: InMemoryStore, client: TestClient
) -> None:
    project_id = await _make_project(store, name="My Project")
    await _make_task(store, project_id, title="Fix the bug", status=ev.BLOCKED)

    with patch(
        "web.routes.blocked._find_last_failure_reason",
        new_callable=AsyncMock,
        return_value="Assertion error in test_foo",
    ):
        response = client.get("/blocked")

    assert response.status_code == 200
    assert "Fix the bug" in response.text
    assert "My Project" in response.text
