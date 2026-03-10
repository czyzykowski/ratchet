"""Unit tests for GET /projects/new and POST /projects."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from core.project_manager import OnboardingError
from core.store import InMemoryStore
from web.routes.projects import router


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


def test_new_project_form_renders(client: TestClient) -> None:
    response = client.get("/projects/new")
    assert response.status_code == 200
    assert "Register Project" in response.text


def test_create_project_success(client: TestClient, store: InMemoryStore) -> None:
    with patch(
        "web.routes.projects.ProjectManager.register_project",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.post(
            "/projects",
            data={"name": "My Project", "path": "/tmp/my-repo"},
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    session = client.get("/_session").json()
    assert "My Project" in session.get("flash", "")
    assert "Project registered" in session.get("flash", "")


def test_create_project_onboarding_error(client: TestClient, store: InMemoryStore) -> None:
    with patch(
        "web.routes.projects.ProjectManager.register_project",
        new_callable=AsyncMock,
        side_effect=OnboardingError("not a git repo"),
    ):
        response = client.post(
            "/projects",
            data={"name": "Bad Project", "path": "/tmp/no-such-dir"},
        )

    assert response.status_code == 400
    assert "not a git repo" in response.text
