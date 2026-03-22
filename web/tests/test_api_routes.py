"""Integration tests for /api/* endpoints per spec requirements."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.models import Project, Spec, Task
from core.store import InMemoryStore
from web.routes.api import events as api_events_router
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
    app.state.sse_queues = set()
    app.state.sse_clients = []
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


# --- Board endpoint tests ---

def test_should_return_board_json_with_columns_in_status_order(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task1_id = uuid4()
    task2_id = uuid4()

    board_tasks = [
        {
            "id": task1_id, "project_id": project_id, "project_name": "My Project",
            "title": "Task 1", "status": ev.READY_FOR_SPEC,
            "has_spec": False, "refinement_count": 0, "updated_at": None,
            "depends_on": [], "required_capabilities": [], "baseline_qa_failure": None,
        },
        {
            "id": task2_id, "project_id": project_id, "project_name": "My Project",
            "title": "Task 2", "status": ev.BLOCKED,
            "has_spec": False, "refinement_count": 0, "updated_at": None,
            "depends_on": [], "required_capabilities": [], "baseline_qa_failure": None,
        },
    ]
    with patch("web.queries.get_board_tasks", new=AsyncMock(return_value=board_tasks)):
        response = client.get("/api/board")

    assert response.status_code == 200
    data = response.json()
    assert "columns" in data
    assert isinstance(data["columns"], list)

    statuses = [c["status"] for c in data["columns"]]
    assert statuses == [
        ev.READY_FOR_SPEC,
        ev.SPEC_QA,
        ev.READY_FOR_IMPLEMENTATION,
        ev.IN_PROGRESS,
        ev.BLOCKED,
        ev.READY_FOR_QA,
        ev.READY_FOR_DEPLOYMENT,
    ]

    columns_by_status = {c["status"]: c for c in data["columns"]}
    rfs = columns_by_status[ev.READY_FOR_SPEC]
    assert len(rfs["tasks"]) == 1
    assert rfs["tasks"][0]["title"] == "Task 1"

    blocked = columns_by_status[ev.BLOCKED]
    assert len(blocked["tasks"]) == 1
    assert blocked["tasks"][0]["title"] == "Task 2"


def test_should_exclude_merged_and_abandoned_tasks_from_board(
    client: TestClient, store: InMemoryStore
) -> None:
    merged_id = uuid4()
    abandoned_id = uuid4()
    # get_board_tasks filters these out — return empty list
    with patch("web.queries.get_board_tasks", new=AsyncMock(return_value=[])):
        response = client.get("/api/board")

    assert response.status_code == 200
    data = response.json()

    all_task_ids = [t["id"] for col in data["columns"] for t in col["tasks"]]
    assert str(merged_id) not in all_task_ids
    assert str(abandoned_id) not in all_task_ids


# --- Task PATCH tests ---

def test_should_rename_task_title_via_patch(
    client: TestClient, store: InMemoryStore
) -> None:
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

    # Re-fetch board to confirm new title via mocked get_board_tasks
    board_tasks = [
        {
            "id": task_id, "project_id": project_id, "project_name": "Test",
            "title": "New Title", "status": ev.READY_FOR_SPEC,
            "has_spec": False, "refinement_count": 0, "updated_at": None,
            "depends_on": [], "required_capabilities": [], "baseline_qa_failure": None,
        }
    ]
    with patch("web.queries.get_board_tasks", new=AsyncMock(return_value=board_tasks)):
        board_response = client.get("/api/board")
    assert board_response.status_code == 200
    board_data = board_response.json()
    all_tasks = [t for col in board_data["columns"] for t in col["tasks"]]
    task_in_board = next(t for t in all_tasks if t["id"] == str(task_id))
    assert task_in_board["title"] == "New Title"


def test_should_return_400_when_patch_title_is_empty(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Some Title")
    )

    response = client.patch(f"/api/tasks/{task_id}", json={"title": ""})
    assert response.status_code == 400


def test_should_return_404_when_patching_unknown_task(
    client: TestClient, store: InMemoryStore
) -> None:
    unknown_id = uuid4()
    response = client.patch(f"/api/tasks/{unknown_id}", json={"title": "Whatever"})
    assert response.status_code == 404


def test_get_task_json_returns_200(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    detail = {
        "task": {
            "id": str(task_id),
            "project_id": str(project_id),
            "title": "Test Task",
            "status": ev.READY_FOR_SPEC,
            "current_spec_id": None,
            "refinement_count": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "depends_on": [],
            "required_capabilities": [],
            "merge_commit_sha": None,
        },
        "project_name": "My Project",
        "specs": [],
        "executions": [],
        "dependencies": [],
        "qa_failure": None,
        "baseline_qa_failure": None,
        "pr_info": None,
        "deploy_hooks": None,
        "feature_id": None,
        "feature_title": None,
    }
    with patch("web.queries.get_task_detail", new=AsyncMock(return_value=detail)):
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
    with patch("web.queries.get_task_detail", new=AsyncMock(return_value=None)):
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


# --- SSE test ---

def test_should_stream_task_updated_event_via_sse(
    client: TestClient, store: InMemoryStore
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


# --- Worker endpoint tests ---

def test_should_return_json_from_post_run_next_when_no_tasks(
    client: TestClient, store: InMemoryStore
) -> None:
    with patch(
        "web.routes.api.worker.get_next_task",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.post("/api/worker/run-next")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "no_tasks_ready"


def test_should_return_json_from_post_run_next_when_task_found(
    client: TestClient, store: InMemoryStore
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    spec_id = uuid4()
    now = datetime.now(UTC)

    fake_task = Task(
        id=task_id,
        project_id=project_id,
        title="My Ready Task",
        status=ev.READY_FOR_IMPLEMENTATION,
        current_spec_id=spec_id,
        refinement_count=0,
        created_at=now,
        updated_at=now,
    )
    fake_project = Project(
        id=project_id,
        name="Test Project",
        repo_url="/tmp/repo",
        local_path="/tmp/repo",
        status="active",
        created_at=now,
        updated_at=now,
    )
    fake_spec = Spec(
        id=spec_id,
        task_id=task_id,
        content="Do the thing",
        previous_spec_id=None,
        created_at=now,
    )

    with (
        patch(
            "web.routes.api.worker.get_next_task",
            new_callable=AsyncMock,
            return_value=(fake_task, fake_project, fake_spec),
        ),
        patch("web.routes.api.worker.run_once", new_callable=AsyncMock, return_value=True),
    ):
        response = client.post("/api/worker/run-next")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert data["task_id"] == str(task_id)


# --- Archive task endpoint tests ---

def test_should_archive_task_when_post_archive_called(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Task to Archive", ev.READY_FOR_SPEC)
    )

    response = client.post(f"/api/tasks/{task_id}/archive")
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["status"] == ev.ABANDONED


def test_should_return_404_when_archiving_unknown_task(
    client: TestClient, store: InMemoryStore
) -> None:
    unknown_id = uuid4()
    response = client.post(f"/api/tasks/{unknown_id}/archive")
    assert response.status_code == 404


def test_should_return_400_when_archiving_already_abandoned_task(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Already Abandoned", ev.ABANDONED)
    )

    response = client.post(f"/api/tasks/{task_id}/archive")
    assert response.status_code == 400


# --- Legacy tests kept for backward compatibility ---

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


def test_should_return_404_when_task_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    unknown_id = uuid4()
    with patch("web.queries.get_task_detail", new=AsyncMock(return_value=None)):
        response = client.get(f"/api/tasks/{unknown_id}")
    assert response.status_code == 404
