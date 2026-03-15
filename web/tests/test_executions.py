"""Unit tests for GET /api/executions/{execution_id} using InMemoryStore (no DB required)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.models import ExecutionTrace
from core.store import InMemoryStore
from web.routes.api.executions import router


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

    response = client.get(f"/executions/{execution_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["execution"]["task_id"] == str(task_id)
    assert data["execution"]["spec_id"] == str(spec_id)
    assert data["execution"]["status"] == "in_progress"
    assert data["execution"]["branch_name"] == "feat/my-task"


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

    response = client.get(f"/executions/{execution_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["execution"]["failure_reason"] == "Tests failed: 3 assertions"


@pytest.mark.asyncio
async def test_should_return_null_trace_when_no_trace_in_store(
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

    response = client.get(f"/executions/{execution_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["trace"] is None


@pytest.mark.asyncio
async def test_should_return_trace_content_when_trace_exists_in_store(
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

    now = datetime.now(UTC)
    store.save_trace(
        ExecutionTrace(
            execution_id=execution_id,
            task_id=task_id,
            spec_id=spec_id,
            content="# Execution Trace\n\nAll good.",
            started_at=now,
            created_at=now,
        )
    )

    response = client.get(f"/executions/{execution_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["trace"] == "# Execution Trace\n\nAll good."
