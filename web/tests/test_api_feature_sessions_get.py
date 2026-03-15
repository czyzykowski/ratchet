"""Unit tests for GET /api/feature-sessions/{session_id}."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.models import ChatSession
from core.store import InMemoryStore
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.state.feature_sessions = {}
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


async def _seed_project(store: InMemoryStore, project_id: UUID) -> None:
    payload = {
        "project_id": str(project_id),
        "name": "Test Project",
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


def _make_chat_session(
    session_id: UUID, messages: list[tuple[str, str, str | None, str | None]]
) -> ChatSession:
    return ChatSession(
        id=session_id,
        session_type="feature",
        context_id=uuid4(),
        context_type="feature",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        messages=messages,
    )


def test_should_return_messages_for_existing_session(client: TestClient) -> None:
    """should return session_id and messages when session exists"""
    session_id = uuid4()
    messages = [
        ("What does it do?", "It helps you build features.", None, None),
        ("Great, generate it.", "## FEATURE READY\n# Feature: Test", None, None),
    ]
    mock_session = _make_chat_session(session_id, messages)

    with patch(
        "web.routes.api.feature_sessions.get_chat_session_by_id",
        new=AsyncMock(return_value=mock_session),
    ):
        response = client.get(f"/api/feature-sessions/{session_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_id)
    assert len(data["messages"]) == 2
    assert data["messages"][0]["content"] == "What does it do?"
    assert data["messages"][0]["assistant"] == "It helps you build features."
    assert data["messages"][1]["content"] == "Great, generate it."


def test_should_return_404_when_session_not_found(client: TestClient) -> None:
    """should return 404 when session does not exist"""
    session_id = uuid4()

    with patch(
        "web.routes.api.feature_sessions.get_chat_session_by_id",
        new=AsyncMock(return_value=None),
    ):
        response = client.get(f"/api/feature-sessions/{session_id}")

    assert response.status_code == 404


def test_should_reflect_session_id_in_feature_detail(
    client: TestClient, store: InMemoryStore
) -> None:
    """should return session_id in GET /api/features/{feature_id} after POST with session_id"""
    project_id = uuid4()
    session_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))

    feature_block = (
        "# Feature: Test Feature\n\n"
        "## Description\nA test feature.\n\n"
        "## High-Level Specs\n"
        "### 1. First Spec\n**Order:** 1\n**Dependencies:** none\n**Content:**\nDo something.\n"
    )
    create_resp = client.post(
        "/api/features",
        json={
            "project_id": str(project_id),
            "feature_block": feature_block,
            "session_id": str(session_id),
        },
    )
    assert create_resp.status_code == 201
    feature_id = create_resp.json()["feature"]["id"]

    get_resp = client.get(f"/api/features/{feature_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["feature"]["session_id"] == str(session_id)
