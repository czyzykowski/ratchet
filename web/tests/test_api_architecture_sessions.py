"""Unit tests for /api/architecture-sessions endpoints."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.claude_repl import _QueueDone
from core.models import ChatSession, ChatSessionSummary
from core.store import InMemoryStore
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.state.architecture_sessions = {}
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
        "name": "test-project",
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


def test_should_create_session_and_return_session_id(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    mock_session = MagicMock()

    with (
        patch(
            "web.routes.api.architecture_sessions.Path.exists",
            return_value=True,
        ),
        patch(
            "web.routes.api.architecture_sessions.Path.read_text",
            return_value="# Intent",
        ),
        patch(
            "web.routes.api.architecture_sessions.SpecReplSession",
            return_value=mock_session,
        ),
    ):
        response = client.post(
            "/api/architecture-sessions",
            json={"project_id": str(project_id)},
        )

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["messages"] == []


def test_should_create_session_with_scope(
    store: InMemoryStore,
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    mock_session = MagicMock()

    app = _make_test_app(store)
    client = TestClient(app)

    with (
        patch(
            "web.routes.api.architecture_sessions.Path.exists",
            return_value=True,
        ),
        patch(
            "web.routes.api.architecture_sessions.Path.read_text",
            return_value="# Intent",
        ),
        patch(
            "web.routes.api.architecture_sessions.SpecReplSession",
            return_value=mock_session,
        ),
    ):
        response = client.post(
            "/api/architecture-sessions",
            json={"project_id": str(project_id), "scope": "Focus on web/ module coupling"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    # Verify a session was stored
    assert len(app.state.architecture_sessions) == 1


def test_should_return_404_when_project_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/architecture-sessions",
        json={"project_id": str(uuid4())},
    )
    assert response.status_code == 404


def test_should_return_messages_for_existing_session(
    client: TestClient,
) -> None:
    session_id = uuid4()
    chat_session = ChatSession(
        id=session_id,
        session_type="architecture",
        context_id=uuid4(),
        context_type="project",
        created_at=datetime.now(tz=UTC),
        messages=[
            ("analyze coupling", "Here is my analysis...", None, None),
        ],
    )

    with patch(
        "web.routes.api.architecture_sessions.get_chat_session_by_id",
        new=AsyncMock(return_value=chat_session),
    ):
        response = client.get(f"/api/architecture-sessions/{session_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_id)
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "analyze coupling"
    assert data["messages"][0]["assistant"] == "Here is my analysis..."


def test_should_return_404_when_session_not_found(client: TestClient) -> None:
    with patch(
        "web.routes.api.architecture_sessions.get_chat_session_by_id",
        new=AsyncMock(return_value=None),
    ):
        response = client.get(f"/api/architecture-sessions/{uuid4()}")

    assert response.status_code == 404


def test_should_delete_session_when_exists(
    store: InMemoryStore,
) -> None:
    app = _make_test_app(store)
    session_id = str(uuid4())
    mock_session = MagicMock()
    mock_session.close = AsyncMock()
    app.state.architecture_sessions[session_id] = mock_session

    client = TestClient(app)
    response = client.delete(f"/api/architecture-sessions/{session_id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert session_id not in app.state.architecture_sessions


def test_should_return_404_when_deleting_missing_session(
    client: TestClient,
) -> None:
    response = client.delete(f"/api/architecture-sessions/{uuid4()}")
    assert response.status_code == 404


def _make_mock_session_with_queue(
    project_id: UUID, chunks: list[object]
) -> MagicMock:
    """Create a mock SpecReplSession that returns a pre-filled queue."""
    session = MagicMock()
    session.task_id = str(project_id)

    async def _ask_detached(
        user_input: str, on_complete: object, **_: object
    ) -> asyncio.Queue:  # type: ignore[type-arg]
        q: asyncio.Queue = asyncio.Queue()  # type: ignore[type-arg]
        for item in chunks:
            q.put_nowait(item)
        q.put_nowait(_QueueDone())
        return q

    session.ask_detached = _ask_detached
    return session


def test_should_execute_action_blocks_in_streamed_response(
    store: InMemoryStore,
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))

    action_text = '```action\n{"action": "create_task", "title": "Decouple modules"}\n```'
    mock_session = _make_mock_session_with_queue(project_id, [action_text])

    app = _make_test_app(store)
    session_id = str(uuid4())
    app.state.architecture_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/architecture-sessions/{session_id}/message",
        json={"user_input": "what should we improve?"},
    )
    assert response.status_code == 200

    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    action_events = [e for e in events if e.get("type") == "action_executed"]
    assert len(action_events) == 1
    assert action_events[0]["action"] == "create_task"
    assert action_events[0]["result"]["success"] is True
    assert "Decouple modules" in action_events[0]["result"]["message"]


def test_should_list_sessions_for_project(
    client: TestClient,
) -> None:
    project_id = uuid4()
    session_id = uuid4()
    sessions = [
        ChatSessionSummary(id=session_id, created_at=datetime.now(tz=UTC)),
    ]

    with patch(
        "web.routes.api.architecture_sessions.get_architecture_sessions_for_project",
        new=AsyncMock(return_value=sessions),
    ):
        response = client.get(
            "/api/architecture-sessions",
            params={"project_id": str(project_id)},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["session_id"] == str(session_id)
    assert "created_at" in data[0]
