"""Integration tests for worker control API endpoints."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.models import Project, Spec, Task
from core.store import InMemoryStore
from web.routes.api.router import api_router
from worker.service import WorkerSettings


class MockWorkerService:
    def __init__(self, status: str = "stopped") -> None:
        self._status = status
        self.started_at: datetime | None = None
        self.error_message: str | None = None
        self._settings = WorkerSettings()
        self.log_buffer = MagicMock()

    @property
    def status(self) -> str:
        return self._status

    @property
    def settings(self) -> WorkerSettings:
        return dataclasses.replace(self._settings)

    async def start(self) -> None:
        if self._status in ("running", "starting"):
            raise RuntimeError("Worker already running")
        self._status = "running"
        self.started_at = datetime.now(UTC)

    async def stop(self, graceful: bool = True) -> None:
        if self._status == "stopped":
            return
        self._status = "stopped"
        self.started_at = None

    async def restart(self, graceful: bool = True) -> None:
        await self.stop(graceful=graceful)
        await self.start()

    async def update_settings(self, settings: WorkerSettings) -> None:
        self._settings = settings


def _make_test_app(store: InMemoryStore, worker_service: MockWorkerService | None = None) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.state.sse_clients = []
    app.state.worker_service = worker_service if worker_service is not None else MockWorkerService()
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def worker_service() -> MockWorkerService:
    return MockWorkerService()


@pytest.fixture
def client(store: InMemoryStore, worker_service: MockWorkerService) -> TestClient:
    return TestClient(_make_test_app(store, worker_service))


# --- run-next tests (preserved) ---


def test_should_return_started_with_task_id_when_task_is_ready(
    client: TestClient, store: InMemoryStore
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    spec_id = uuid4()
    now = datetime.now(UTC)

    fake_task = Task(
        id=task_id,
        project_id=project_id,
        title="Ready Task",
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
    assert data["title"] == "Ready Task"


def test_should_return_no_task_when_no_ready_task(
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


# --- GET /status ---


def test_should_return_stopped_status_when_worker_is_stopped(
    client: TestClient, worker_service: MockWorkerService
) -> None:
    response = client.get("/api/worker/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"
    assert data["started_at"] is None
    assert data["uptime_seconds"] is None
    assert data["error_message"] is None
    assert "settings" in data


def test_should_return_running_status_with_uptime_when_worker_is_running(
    store: InMemoryStore,
) -> None:
    ws = MockWorkerService(status="running")
    ws.started_at = datetime.now(UTC)
    client = TestClient(_make_test_app(store, ws))

    response = client.get("/api/worker/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["started_at"] is not None
    assert data["uptime_seconds"] is not None
    assert data["uptime_seconds"] >= 0


# --- POST /start ---


def test_should_return_200_when_starting_stopped_worker(
    client: TestClient, worker_service: MockWorkerService
) -> None:
    response = client.post("/api/worker/start")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"


def test_should_return_409_when_starting_already_running_worker(
    store: InMemoryStore,
) -> None:
    ws = MockWorkerService(status="running")
    ws.started_at = datetime.now(UTC)
    client = TestClient(_make_test_app(store, ws))

    response = client.post("/api/worker/start")
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


# --- POST /stop ---


def test_should_return_200_when_stopping_running_worker(
    store: InMemoryStore,
) -> None:
    ws = MockWorkerService(status="running")
    ws.started_at = datetime.now(UTC)
    client = TestClient(_make_test_app(store, ws))

    response = client.post("/api/worker/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"


def test_should_return_409_when_stopping_already_stopped_worker(
    client: TestClient, worker_service: MockWorkerService
) -> None:
    response = client.post("/api/worker/stop")
    assert response.status_code == 409
    assert "already stopped" in response.json()["detail"]


def test_should_pass_graceful_false_when_stopping_with_graceful_false(
    store: InMemoryStore,
) -> None:
    ws = MockWorkerService(status="running")
    ws.started_at = datetime.now(UTC)
    stop_calls: list[bool] = []

    original_stop = ws.stop

    async def capturing_stop(graceful: bool = True) -> None:
        stop_calls.append(graceful)
        await original_stop(graceful=graceful)

    ws.stop = capturing_stop  # type: ignore[method-assign]
    client = TestClient(_make_test_app(store, ws))

    response = client.post("/api/worker/stop", json={"graceful": False})
    assert response.status_code == 200
    assert stop_calls == [False]


# --- POST /restart ---


def test_should_return_200_when_restarting_worker(
    store: InMemoryStore,
) -> None:
    ws = MockWorkerService(status="running")
    ws.started_at = datetime.now(UTC)
    client = TestClient(_make_test_app(store, ws))

    response = client.post("/api/worker/restart")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"


# --- PATCH /settings ---


def test_should_return_200_with_updated_settings_when_partial_update(
    client: TestClient, worker_service: MockWorkerService
) -> None:
    response = client.patch("/api/worker/settings", json={"max_workers": 2})
    assert response.status_code == 200
    data = response.json()
    assert data["max_workers"] == 2
    # Other defaults preserved
    assert data["watchdog_timeout"] == 300
    assert data["enabled"] is True


def test_should_return_422_when_invalid_field_type_in_settings(
    client: TestClient,
) -> None:
    response = client.patch("/api/worker/settings", json={"max_workers": "not_a_number"})
    assert response.status_code == 422
