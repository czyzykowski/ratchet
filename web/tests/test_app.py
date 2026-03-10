"""Smoke tests for web/app.py — all 5 HTML routes."""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient


@asynccontextmanager
async def _noop_lifespan(app: object) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture()
def client() -> TestClient:
    with (
        patch("core.db.get_pool", new_callable=AsyncMock),
        patch("core.db.close_pool", new_callable=AsyncMock),
    ):
        from web.app import app

        app.router.lifespan_context = _noop_lifespan
        return TestClient(app, raise_server_exceptions=True)


def test_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200


def test_board(client: TestClient) -> None:
    response = client.get("/board")
    assert response.status_code == 200


def test_projects(client: TestClient) -> None:
    response = client.get("/projects")
    assert response.status_code == 200


def test_blocked(client: TestClient) -> None:
    response = client.get("/blocked")
    assert response.status_code == 200


def test_features(client: TestClient) -> None:
    response = client.get("/features")
    assert response.status_code == 200
