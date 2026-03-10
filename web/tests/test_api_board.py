"""Unit tests for GET /api/board."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


async def _seed_project(store: InMemoryStore, project_id: UUID, name: str = "Test Project") -> None:
    payload = {
        "project_id": str(project_id),
        "name": name,
        "repo_url": "/tmp/test",
        "local_path": "/tmp/test",
        "status": "active",
    }
    await store.append_event(
        aggregate_id=_REGISTRY_ID,
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


async def _seed_task(
    store: InMemoryStore,
    task_id: UUID,
    project_id: UUID,
    title: str,
    status: str = ev.READY_FOR_SPEC,
) -> None:
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload={"task_id": str(task_id), "project_id": str(project_id), "title": title},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": title,
            "status": status,
        },
    )


def test_should_return_board_grouped_by_status_when_tasks_exist(
    client: TestClient, store: InMemoryStore
) -> None:
    import asyncio

    project_id = uuid4()
    task_id = uuid4()

    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    response = client.get("/api/board")
    assert response.status_code == 200
    data = response.json()

    assert "groups" in data
    groups = {g["status"]: g for g in data["groups"]}

    assert ev.READY_FOR_SPEC in groups
    rfs_group = groups[ev.READY_FOR_SPEC]
    assert rfs_group["label"] == "READY FOR SPEC"
    assert len(rfs_group["tasks"]) == 1
    assert rfs_group["tasks"][0]["title"] == "My Task"
    assert rfs_group["tasks"][0]["project_name"] == "Test Project"
    assert rfs_group["tasks"][0]["unmet_dependencies"] == []


def test_should_return_empty_groups_when_no_tasks(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get("/api/board")
    assert response.status_code == 200
    data = response.json()

    assert "groups" in data
    for group in data["groups"]:
        assert group["tasks"] == []
