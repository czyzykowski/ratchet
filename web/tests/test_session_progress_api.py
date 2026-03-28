"""Tests for GET /api/executions/{execution_id}/session-progress."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.store import InMemoryStore
from orchestrator.registry import WorkerRegistry
from web.routes.api.executions import router


def _make_app(store: InMemoryStore, registry: WorkerRegistry) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.registry = registry
    app.include_router(router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def registry() -> WorkerRegistry:
    return WorkerRegistry()


@pytest.fixture
def client(store: InMemoryStore, registry: WorkerRegistry) -> TestClient:
    return TestClient(_make_app(store, registry))


def test_should_return_404_when_no_worker_running_execution(
    client: TestClient,
) -> None:
    execution_id = uuid4()
    response = client.get(f"/executions/{execution_id}/session-progress")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data or "detail" in data


def test_should_return_session_progress_when_worker_found(
    store: InMemoryStore, registry: WorkerRegistry
) -> None:
    from core.remote_protocol import GetSessionProgressResponse

    execution_id = uuid4()
    execution_id_str = str(execution_id)

    # Register a worker and assign the execution to it
    mock_ws = MagicMock()
    conn = registry.register("worker-1", ["default"], mock_ws)
    registry.assign_job("worker-1", execution_id_str)

    # Create a mock channel that returns a valid session progress response
    mock_channel = AsyncMock()
    mock_channel.send_command = AsyncMock(
        return_value=GetSessionProgressResponse(
            type="get_session_progress_response",
            request_id="req-1",
            success=True,
            messages=[{"type": "assistant", "content": "hello"}],
            total_messages=3,
            file_size_bytes=256,
        )
    )
    conn.channel = mock_channel

    app = _make_app(store, registry)
    client = TestClient(app)

    response = client.get(f"/executions/{execution_id}/session-progress")
    assert response.status_code == 200
    data = response.json()
    assert data["messages"] == [{"type": "assistant", "content": "hello"}]
    assert data["total_messages"] == 3
    assert data["file_size_bytes"] == 256


def test_should_return_404_when_worker_has_no_channel(
    store: InMemoryStore, registry: WorkerRegistry
) -> None:
    execution_id = uuid4()
    execution_id_str = str(execution_id)

    mock_ws = MagicMock()
    registry.register("worker-1", ["default"], mock_ws)
    registry.assign_job("worker-1", execution_id_str)
    # channel is None by default (not set)

    app = _make_app(store, registry)
    client = TestClient(app)

    response = client.get(f"/executions/{execution_id}/session-progress")
    assert response.status_code == 404
