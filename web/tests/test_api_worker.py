"""Unit tests for POST /api/worker/run-next."""

from __future__ import annotations

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
    assert data["status"] == "no_task"
