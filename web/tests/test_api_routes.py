"""Integration tests for all /api/* endpoints using InMemoryStore."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.api import events as api_events_router
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.state.sse_queues: set[asyncio.Queue[str]] = set()
    app.include_router(api_router)
    app.include_router(api_events_router.router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


async def _seed_project(store: InMemoryStore, project_id: UUID, name: str = "Test") -> None:
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


async def _seed_feature(
    store: InMemoryStore, feature_id: UUID, project_id: UUID, title: str
) -> None:
    payload = {
        "feature_id": str(feature_id),
        "project_id": str(project_id),
        "title": title,
        "description": f"Description for {title}",
    }
    await store.append_event(
        aggregate_id=feature_id,
        aggregate_type="feature",
        event_type=ev.FEATURE_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_features",
        event_type=ev.FEATURE_CREATED,
        payload=payload,
    )


def test_should_return_200_with_groups_from_board_endpoint(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get("/api/board")
    assert response.status_code == 200
    data = response.json()
    assert "groups" in data
    assert isinstance(data["groups"], list)


def test_should_return_200_with_projects_list(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id, "My Project"))

    response = client.get("/api/projects")
    assert response.status_code == 200
    data = response.json()
    assert "projects" in data
    assert isinstance(data["projects"], list)
    assert len(data["projects"]) == 1


def test_should_return_200_with_features_list(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    feature_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_feature(store, feature_id, project_id, "My Feature")
    )

    response = client.get("/api/features")
    assert response.status_code == 200
    data = response.json()
    assert "features" in data
    assert isinstance(data["features"], list)


def test_should_return_404_when_task_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    unknown_id = uuid4()
    response = client.get(f"/api/tasks/{unknown_id}")
    assert response.status_code == 404


def test_get_task_json_returns_200(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id, "My Project"))
    asyncio.get_event_loop().run_until_complete(
        store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_CREATED,
            payload={
                "task_id": str(task_id),
                "project_id": str(project_id),
                "title": "Test Task",
                "status": ev.READY_FOR_SPEC,
            },
        )
    )

    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert "task" in data
    assert "specs" in data
    assert "executions" in data
    assert "qa_failure" in data
    assert data["task"]["id"] == str(task_id)
    assert data["task"]["title"] == "Test Task"


def test_get_task_json_404(client: TestClient, store: InMemoryStore) -> None:
    unknown_id = uuid4()
    response = client.get(f"/api/tasks/{unknown_id}")
    assert response.status_code == 404


async def test_task_sse_stream_emits_status() -> None:
    from web.routes.api.tasks import _task_status_generator

    store = InMemoryStore()
    task_id = uuid4()
    project_id = uuid4()

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": "SSE Task",
            "status": ev.READY_FOR_SPEC,
        },
    )

    disconnect_calls = 0

    class FakeRequest:
        async def is_disconnected(self) -> bool:
            nonlocal disconnect_calls
            disconnect_calls += 1
            # disconnect after one poll
            return disconnect_calls > 1

    gen = _task_status_generator(store, task_id, FakeRequest())
    first_event = await gen.__anext__()
    assert "status" in first_event
    import json as _json
    payload = _json.loads(first_event.removeprefix("data: ").strip())
    assert "status" in payload


def test_should_return_text_event_stream_content_type_for_sse_endpoint(
    client: TestClient,
) -> None:
    from web.routes.api.events import sse_events

    queues: set[asyncio.Queue[str]] = set()

    class FakeState:
        sse_queues = queues

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()

    async def _get_media_type() -> str:
        response = await sse_events(FakeRequest())  # type: ignore[arg-type]
        return response.media_type or ""

    media_type = asyncio.get_event_loop().run_until_complete(_get_media_type())
    assert "text/event-stream" in media_type
