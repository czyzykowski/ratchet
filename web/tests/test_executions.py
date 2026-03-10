"""Unit tests for GET /executions/{execution_id} using InMemoryStore (no DB required)."""

from __future__ import annotations

import tempfile
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.executions import router


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    test_app = FastAPI()
    test_app.state.store = store
    test_app.include_router(router)
    return TestClient(test_app)


def test_should_return_404_when_execution_does_not_exist(
    client: TestClient,
) -> None:
    missing_id = uuid4()
    with patch("web.routes.executions.get_traces_dir", return_value="/tmp/no-such-dir"):
        response = client.get(f"/executions/{missing_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_should_return_200_with_execution_fields_when_started_event_exists(
    store: InMemoryStore, client: TestClient
) -> None:
    execution_id = uuid4()
    task_id = uuid4()
    spec_id = uuid4()

    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload={
            "task_id": str(task_id),
            "spec_id": str(spec_id),
            "branch_name": "feat/my-task",
            "started_at": "2024-01-01T00:00:00",
        },
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("web.routes.executions.get_traces_dir", return_value=tmpdir):
            response = client.get(f"/executions/{execution_id}")

    assert response.status_code == 200
    assert str(task_id) in response.text
    assert str(spec_id) in response.text
    assert "in_progress" in response.text
    assert "feat/my-task" in response.text


@pytest.mark.asyncio
async def test_should_show_failure_reason_when_execution_failed_event_exists(
    store: InMemoryStore, client: TestClient
) -> None:
    execution_id = uuid4()
    task_id = uuid4()
    spec_id = uuid4()

    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload={
            "task_id": str(task_id),
            "spec_id": str(spec_id),
            "branch_name": "feat/my-task",
            "started_at": "2024-01-01T00:00:00",
        },
    )
    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_FAILED,
        payload={
            "failure_reason": "Tests failed: 3 assertions",
            "completed_at": "2024-01-01T01:00:00",
        },
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("web.routes.executions.get_traces_dir", return_value=tmpdir):
            response = client.get(f"/executions/{execution_id}")

    assert response.status_code == 200
    assert "Tests failed: 3 assertions" in response.text


@pytest.mark.asyncio
async def test_should_show_no_trace_available_when_trace_file_does_not_exist(
    store: InMemoryStore, client: TestClient
) -> None:
    execution_id = uuid4()
    task_id = uuid4()
    spec_id = uuid4()

    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload={
            "task_id": str(task_id),
            "spec_id": str(spec_id),
            "branch_name": "feat/my-task",
            "started_at": "2024-01-01T00:00:00",
        },
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("web.routes.executions.get_traces_dir", return_value=tmpdir):
            response = client.get(f"/executions/{execution_id}")

    assert response.status_code == 200
    assert "No trace available" in response.text
