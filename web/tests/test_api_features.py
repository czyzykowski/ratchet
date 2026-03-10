"""Unit tests for /api/features endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


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


async def _seed_project(store: InMemoryStore, project_id: UUID, name: str = "Test") -> None:
    payload = {
        "project_id": str(project_id),
        "name": name,
        "repo_url": "/tmp/test",
        "local_path": "/tmp/test",
        "status": "active",
    }
    await store.append_event(
        aggregate_id=_REGISTRY_ID,
        aggregate_type="projects",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )


async def _seed_feature(
    store: InMemoryStore, feature_id: UUID, project_id: UUID, title: str
) -> None:
    payload = {
        "feature_id": str(feature_id),
        "project_id": str(project_id),
        "title": title,
        "description": f"Description for {title}",
    }
    await store.append_event(
        aggregate_id=feature_id,
        aggregate_type="feature",
        event_type=ev.FEATURE_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_features",
        event_type=ev.FEATURE_CREATED,
        payload=payload,
    )


def test_should_return_features_list(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    feature_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id, "My Project"))
    asyncio.get_event_loop().run_until_complete(
        _seed_feature(store, feature_id, project_id, "My Feature")
    )

    response = client.get("/api/features")
    assert response.status_code == 200
    data = response.json()
    assert "features" in data
    assert len(data["features"]) == 1
    assert data["features"][0]["title"] == "My Feature"
    assert data["features"][0]["project_name"] == "My Project"


def test_should_return_feature_detail_when_feature_exists(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    feature_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_feature(store, feature_id, project_id, "My Feature")
    )

    response = client.get(f"/api/features/{feature_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["feature"]["id"] == str(feature_id)
    assert data["feature"]["title"] == "My Feature"
    assert data["specs"] == []


def test_should_return_404_when_feature_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get(f"/api/features/{uuid4()}")
    assert response.status_code == 404
