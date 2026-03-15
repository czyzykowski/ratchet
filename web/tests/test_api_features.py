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


_FEATURE_BLOCK = """# Feature: My Feature

## Description
A description.

## High-Level Specs
### 1. Spec One
**Order:** 1
**Dependencies:** none
**Content:**
Some content here.
"""


def test_should_store_session_id_when_provided_to_post_features(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    session_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))

    response = client.post(
        "/api/features",
        json={
            "project_id": str(project_id),
            "feature_block": _FEATURE_BLOCK,
            "session_id": str(session_id),
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["feature"]["session_id"] == str(session_id)


def test_should_reflect_session_id_in_get_feature_response(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    session_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))

    post_resp = client.post(
        "/api/features",
        json={
            "project_id": str(project_id),
            "feature_block": _FEATURE_BLOCK,
            "session_id": str(session_id),
        },
    )
    assert post_resp.status_code == 201
    feature_id = post_resp.json()["feature"]["id"]

    get_resp = client.get(f"/api/features/{feature_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["feature"]["session_id"] == str(session_id)


def test_should_include_status_in_features_list(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    feature_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_feature(store, feature_id, project_id, "My Feature")
    )

    response = client.get("/api/features")
    assert response.status_code == 200
    data = response.json()
    assert data["features"][0]["status"] == "idea"


def test_should_include_status_in_feature_detail(
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
    assert data["feature"]["status"] == "idea"


def test_should_return_in_clarification_status_when_chat_session_exists(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    feature_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_feature(store, feature_id, project_id, "My Feature")
    )
    asyncio.get_event_loop().run_until_complete(
        store.append_event(
            aggregate_id=feature_id,
            aggregate_type="feature",
            event_type=ev.CHAT_SESSION_CREATED,
            payload={"feature_id": str(feature_id), "session_id": str(uuid4())},
        )
    )

    response = client.get(f"/api/features/{feature_id}")
    assert response.status_code == 200
    assert response.json()["feature"]["status"] == "in_clarification"


def test_should_return_defined_status_when_hls_added(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    feature_id = uuid4()
    hls_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_feature(store, feature_id, project_id, "My Feature")
    )
    asyncio.get_event_loop().run_until_complete(
        store.append_event(
            aggregate_id=feature_id,
            aggregate_type="feature",
            event_type=ev.HIGH_LEVEL_SPEC_ADDED,
            payload={
                "hls_id": str(hls_id),
                "feature_id": str(feature_id),
                "title": "Spec One",
                "order": 1,
                "content": "Some content.",
                "dependencies": [],
            },
        )
    )

    response = client.get(f"/api/features/{feature_id}")
    assert response.status_code == 200
    assert response.json()["feature"]["status"] == "defined"
