"""Unit tests for /api/tasks endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch
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
    app.state.sse_clients = []
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


async def _seed_project(store: InMemoryStore, project_id: UUID) -> None:
    payload = {
        "project_id": str(project_id),
        "name": "Test Project",
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


def test_should_return_task_detail_with_specs_and_executions_when_task_exists(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(_seed_task(store, task_id, project_id, "My Task"))

    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["id"] == str(task_id)
    assert data["task"]["title"] == "My Task"
    assert data["specs"] == []
    assert data["executions"] == []
    assert data["qa_failure"] is None


def test_should_return_404_when_task_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get(f"/api/tasks/{uuid4()}")
    assert response.status_code == 404


def test_should_create_task_and_return_201(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))

    response = client.post(
        "/api/tasks", json={"project_id": str(project_id), "title": "New Task"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["task"]["title"] == "New Task"
    assert data["task"]["status"] == ev.READY_FOR_SPEC
    assert data["task"]["project_id"] == str(project_id)


def test_should_update_task_title(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Old Title")
    )

    response = client.patch(f"/api/tasks/{task_id}", json={"title": "New Title"})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Title"
    assert data["id"] == str(task_id)


def test_should_assign_spec_to_task(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    response = client.post(
        f"/api/tasks/{task_id}/spec", json={"content": "Do the thing"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["status"] == ev.READY_FOR_IMPLEMENTATION
    assert data["spec"]["content"] == "Do the thing"
    assert data["spec"]["task_id"] == str(task_id)


def test_should_reset_blocked_task(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    async def _block_task() -> None:
        # Transition through states to reach BLOCKED
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={
                "from_status": ev.READY_FOR_SPEC,
                "to_status": ev.SPEC_QA,
                "status": ev.SPEC_QA,
            },
        )
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={
                "from_status": ev.SPEC_QA,
                "to_status": ev.READY_FOR_IMPLEMENTATION,
                "status": ev.READY_FOR_IMPLEMENTATION,
            },
        )
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={
                "from_status": ev.READY_FOR_IMPLEMENTATION,
                "to_status": ev.IN_PROGRESS,
                "status": ev.IN_PROGRESS,
            },
        )
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={
                "from_status": ev.IN_PROGRESS,
                "to_status": ev.BLOCKED,
                "status": ev.BLOCKED,
            },
        )

    asyncio.get_event_loop().run_until_complete(_block_task())

    response = client.post(f"/api/tasks/{task_id}/reset")
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["status"] == ev.READY_FOR_IMPLEMENTATION


def test_should_return_400_when_resetting_non_blocked_task(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    response = client.post(f"/api/tasks/{task_id}/reset")
    assert response.status_code == 400


def test_should_deploy_task(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    execution_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    async def _setup_deploy() -> None:
        # Advance to READY_FOR_DEPLOYMENT
        for from_s, to_s in [
            (ev.READY_FOR_SPEC, ev.SPEC_QA),
            (ev.SPEC_QA, ev.READY_FOR_IMPLEMENTATION),
            (ev.READY_FOR_IMPLEMENTATION, ev.IN_PROGRESS),
            (ev.IN_PROGRESS, ev.READY_FOR_QA),
            (ev.READY_FOR_QA, ev.READY_FOR_DEPLOYMENT),
        ]:
            await store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_STATUS_CHANGED,
                payload={"from_status": from_s, "to_status": to_s, "status": to_s},
            )
        # Add execution branch record
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_executions",
            event_type=ev.EXECUTION_STARTED,
            payload={
                "execution_id": str(execution_id),
                "task_id": str(task_id),
                "spec_id": str(uuid4()),
                "branch_name": f"execution/{execution_id}",
                "status": "completed",
            },
        )

    asyncio.get_event_loop().run_until_complete(_setup_deploy())

    with patch("web.routes.api.tasks.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        response = client.post(f"/api/tasks/{task_id}/deploy")

    assert response.status_code == 200
    data = response.json()
    assert data["task"]["status"] == ev.DEPLOYED
    assert mock_run.call_count >= 3  # checkout, merge, commit
