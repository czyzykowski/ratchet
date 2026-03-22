"""Integration tests for worker control API endpoints."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.store import InMemoryStore
from web.local_worker import LocalWorkerSettings
from web.routes.api.router import api_router


class MockLocalWorkerManager:
    def __init__(self, status: str = "stopped") -> None:
        self._status = status
        self.started_at: datetime | None = None
        self.error_message: str | None = None
        self.pid: int | None = None
        self._settings = LocalWorkerSettings()
        self.log_buffer = MagicMock()

    @property
    def status(self) -> str:
        return self._status

    @property
    def settings(self) -> LocalWorkerSettings:
        return dataclasses.replace(self._settings)

    async def start(self) -> None:
        if self._status in ("running", "starting"):
            raise RuntimeError("Worker already running")
        self._status = "running"
        self.started_at = datetime.now(UTC)
        self.pid = 99999

    async def stop(self, graceful: bool = True) -> None:
        if self._status == "stopped":
            return
        self._status = "stopped"
        self.started_at = None
        self.pid = None

    async def restart(self, graceful: bool = True) -> None:
        await self.stop(graceful=graceful)
        await self.start()

    async def update_settings(self, settings: LocalWorkerSettings) -> None:
        self._settings = settings


def _make_test_app(
    store: InMemoryStore, worker_service: MockLocalWorkerManager | None = None
) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.state.sse_clients = []
    app.state.registry = MagicMock()
    app.state.registry.all_workers.return_value = []
    app.state.worker_service = (
        worker_service if worker_service is not None else MockLocalWorkerManager()
    )
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def worker_service() -> MockLocalWorkerManager:
    return MockLocalWorkerManager()


@pytest.fixture
def client(store: InMemoryStore, worker_service: MockLocalWorkerManager) -> TestClient:
    return TestClient(_make_test_app(store, worker_service))


# --- GET /status ---


def test_should_return_stopped_status_when_worker_is_stopped(
    client: TestClient, worker_service: MockLocalWorkerManager
) -> None:
    response = client.get("/api/worker/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"
    assert data["started_at"] is None
    assert data["uptime_seconds"] is None
    assert data["error_message"] is None
    assert data["pid"] is None
    assert "settings" in data


def test_should_return_running_status_with_uptime_when_worker_is_running(
    store: InMemoryStore,
) -> None:
    ws = MockLocalWorkerManager(status="running")
    ws.started_at = datetime.now(UTC)
    ws.pid = 99999
    client = TestClient(_make_test_app(store, ws))

    response = client.get("/api/worker/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["started_at"] is not None
    assert data["uptime_seconds"] is not None
    assert data["uptime_seconds"] >= 0
    assert data["pid"] == 99999


# --- POST /start ---


def test_should_return_200_when_starting_stopped_worker(
    client: TestClient, worker_service: MockLocalWorkerManager
) -> None:
    response = client.post("/api/worker/start")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"


def test_should_return_409_when_starting_already_running_worker(
    store: InMemoryStore,
) -> None:
    ws = MockLocalWorkerManager(status="running")
    ws.started_at = datetime.now(UTC)
    client = TestClient(_make_test_app(store, ws))

    response = client.post("/api/worker/start")
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


# --- POST /stop ---


def test_should_return_200_when_stopping_running_worker(
    store: InMemoryStore,
) -> None:
    ws = MockLocalWorkerManager(status="running")
    ws.started_at = datetime.now(UTC)
    client = TestClient(_make_test_app(store, ws))

    response = client.post("/api/worker/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"


def test_should_return_409_when_stopping_already_stopped_worker(
    client: TestClient, worker_service: MockLocalWorkerManager
) -> None:
    response = client.post("/api/worker/stop")
    assert response.status_code == 409
    assert "already stopped" in response.json()["detail"]


def test_should_pass_graceful_false_when_stopping_with_graceful_false(
    store: InMemoryStore,
) -> None:
    ws = MockLocalWorkerManager(status="running")
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
    ws = MockLocalWorkerManager(status="running")
    ws.started_at = datetime.now(UTC)
    client = TestClient(_make_test_app(store, ws))

    response = client.post("/api/worker/restart")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"


# --- PATCH /settings ---


def test_should_return_200_with_updated_settings_when_partial_update(
    client: TestClient, worker_service: MockLocalWorkerManager
) -> None:
    response = client.patch("/api/worker/settings", json={"enabled": False})
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    # Other defaults preserved
    assert data["capabilities"] == []
    assert data["port"] == 8000


def test_should_return_422_when_invalid_field_type_in_settings(
    client: TestClient,
) -> None:
    response = client.patch("/api/worker/settings", json={"port": "not_a_number"})
    assert response.status_code == 422
