"""Unit tests for GET /api/feature-sessions/{session_id}."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.models import ChatSession
from core.store import InMemoryStore
from web.routes.api.router import api_router


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


def test_should_return_messages_for_existing_session(
    client: TestClient,
) -> None:
    session_id = uuid4()
    chat_session = ChatSession(
        id=session_id,
        session_type="feature",
        context_id=uuid4(),
        context_type="feature",
        created_at=datetime.now(tz=UTC),
        messages=[
            ("hello", "hi there", None, None),
        ],
    )

    with patch(
        "web.routes.api.feature_sessions.get_chat_session_by_id",
        new=AsyncMock(return_value=chat_session),
    ):
        response = client.get(f"/api/feature-sessions/{session_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_id)
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "hello"
    assert data["messages"][0]["assistant"] == "hi there"


def test_should_return_404_when_session_not_found(client: TestClient) -> None:
    with patch(
        "web.routes.api.feature_sessions.get_chat_session_by_id",
        new=AsyncMock(return_value=None),
    ):
        response = client.get(f"/api/feature-sessions/{uuid4()}")

    assert response.status_code == 404
