"""Unit tests for GET /api/specs/{spec_id} endpoint."""

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


async def _seed_spec(
    store: InMemoryStore, spec_id: UUID, task_id: UUID, content: str
) -> None:
    payload = {
        "spec_id": str(spec_id),
        "task_id": str(task_id),
        "content": content,
        "previous_spec_id": None,
    }
    await store.append_event(
        aggregate_id=spec_id,
        aggregate_type="spec",
        event_type=ev.SPEC_CREATED,
        payload=payload,
    )


def test_should_return_spec_when_spec_exists(
    client: TestClient, store: InMemoryStore
) -> None:
    spec_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(
        _seed_spec(store, spec_id, task_id, "# My Spec\n\nDo the thing.")
    )

    response = client.get(f"/api/specs/{spec_id}")
    assert response.status_code == 200
    data = response.json()
    assert "spec" in data
    assert data["spec"]["id"] == str(spec_id)
    assert data["spec"]["task_id"] == str(task_id)
    assert data["spec"]["content"] == "# My Spec\n\nDo the thing."
    assert data["spec"]["created_at"] is not None


def test_should_return_404_when_spec_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get(f"/api/specs/{uuid4()}")
    assert response.status_code == 404
