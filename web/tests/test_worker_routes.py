"""Unit tests for POST /worker/run-next."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from core import events as ev
from core.models import Project, Spec, Task
from core.store import InMemoryStore
from web.routes.worker import router


def _make_test_app(store: InMemoryStore) -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(SessionMiddleware, secret_key="test-secret")
    test_app.state.store = store
    test_app.state.pool = MagicMock()
    test_app.include_router(router)

    @test_app.get("/_session")
    async def session_peek(request: Request) -> JSONResponse:
        return JSONResponse(dict(request.session))

    return test_app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store), follow_redirects=False)


def test_run_next_no_task(client: TestClient, store: InMemoryStore) -> None:
    with (
        patch("web.routes.worker.PostgresStore") as mock_store_cls,
        patch("web.routes.worker.get_next_task", new_callable=AsyncMock, return_value=None),
    ):
        mock_store_cls.return_value = store
        response = client.post("/worker/run-next")

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    session = client.get("/_session").json()
    assert "No tasks ready" in session.get("flash", "")


def test_run_next_picks_task(client: TestClient, store: InMemoryStore) -> None:
    task_id = uuid4()
    project_id = uuid4()
    spec_id = uuid4()
    now = datetime.now(UTC)

    fake_task = Task(
        id=task_id,
        project_id=project_id,
        title="My important task",
        status=ev.READY_FOR_IMPLEMENTATION,
        current_spec_id=spec_id,
        refinement_count=0,
        created_at=now,
        updated_at=now,
    )
    fake_project = Project(
        id=project_id,
        name="My Project",
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
        patch("web.routes.worker.PostgresStore") as mock_store_cls,
        patch(
            "web.routes.worker.get_next_task",
            new_callable=AsyncMock,
            return_value=(fake_task, fake_project, fake_spec),
        ),
        patch("web.routes.worker.run_once", new_callable=AsyncMock, return_value=True),
    ):
        mock_store_cls.return_value = store
        response = client.post("/worker/run-next")

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    session = client.get("/_session").json()
    assert "My important task" in session.get("flash", "")
