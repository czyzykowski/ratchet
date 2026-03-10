"""Unit tests for POST /tasks/{id}/reset."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.tasks import router


def _make_test_app(store: InMemoryStore) -> FastAPI:
    test_app = FastAPI()
    test_app.state.store = store
    test_app.state.pool = MagicMock()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store), follow_redirects=False)


async def _setup_task(
    store: InMemoryStore,
    task_id: UUID,
    project_id: UUID,
    title: str,
    status: str,
) -> None:
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": title,
            "status": ev.READY_FOR_SPEC,
        },
    )
    if status != ev.READY_FOR_SPEC:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={"from_status": ev.READY_FOR_SPEC, "to_status": status},
        )


def _fake_task(task_id: UUID, project_id: UUID, title: str, status: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": task_id,
        "project_id": project_id,
        "title": title,
        "status": status,
        "current_spec_id": None,
        "refinement_count": 0,
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.asyncio
async def test_reset_blocked_task(store: InMemoryStore, client: TestClient) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(store, task_id, project_id, "My Task", ev.BLOCKED)

    with patch("web.routes.tasks.PostgresStore") as mock_store_cls:
        mock_store_cls.return_value = store
        response = client.post(f"/tasks/{task_id}/reset")

    assert response.status_code == 303
    assert response.headers["location"] == f"/tasks/{task_id}"

    from core.state_machine import TaskStateMachine

    sm = TaskStateMachine(store)
    current_status = await sm.get_current_status(task_id)
    assert current_status == ev.READY_FOR_IMPLEMENTATION


@pytest.mark.asyncio
async def test_reset_non_blocked_task(store: InMemoryStore, client: TestClient) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(store, task_id, project_id, "My Task", ev.IN_PROGRESS)

    with patch("web.routes.tasks.PostgresStore") as mock_store_cls:
        mock_store_cls.return_value = store
        response = client.post(f"/tasks/{task_id}/reset")

    assert response.status_code == 400
