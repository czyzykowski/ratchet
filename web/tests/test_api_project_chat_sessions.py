"""Unit tests for /api/project-chat-sessions endpoints."""

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
from core.models import ChatSession
from core.store import InMemoryStore
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.state.project_chat_sessions = {}
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
            "web.routes.api.project_chat_sessions.Path.exists",
            return_value=True,
        ),
        patch(
            "web.routes.api.project_chat_sessions.Path.read_text",
            return_value="# Intent",
        ),
        patch(
            "web.routes.api.project_chat_sessions._gather_project_context",
            new=AsyncMock(return_value=("# Intent", "", [], [], [])),
        ),
        patch(
            "web.routes.api.project_chat_sessions.SpecReplSession",
            return_value=mock_session,
        ),
    ):
        response = client.post(
            "/api/project-chat-sessions",
            json={"project_id": str(project_id)},
        )

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["messages"] == []


def test_should_return_404_when_project_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/project-chat-sessions",
        json={"project_id": str(uuid4())},
    )
    assert response.status_code == 404


def test_should_return_messages_for_existing_session(
    client: TestClient,
) -> None:
    session_id = uuid4()
    chat_session = ChatSession(
        id=session_id,
        session_type="project_chat",
        context_id=uuid4(),
        context_type="project",
        created_at=datetime.now(tz=UTC),
        messages=[
            ("hello", "hi there", None, None),
        ],
    )

    with patch(
        "web.routes.api.project_chat_sessions.get_chat_session_by_id",
        new=AsyncMock(return_value=chat_session),
    ):
        response = client.get(f"/api/project-chat-sessions/{session_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_id)
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "hello"
    assert data["messages"][0]["assistant"] == "hi there"


def test_should_return_404_when_session_not_found(client: TestClient) -> None:
    with patch(
        "web.routes.api.project_chat_sessions.get_chat_session_by_id",
        new=AsyncMock(return_value=None),
    ):
        response = client.get(f"/api/project-chat-sessions/{uuid4()}")

    assert response.status_code == 404


def test_should_delete_session_when_exists(
    store: InMemoryStore,
) -> None:
    app = _make_test_app(store)
    session_id = str(uuid4())
    mock_session = MagicMock()
    mock_session.close = AsyncMock()
    app.state.project_chat_sessions[session_id] = mock_session

    client = TestClient(app)
    response = client.delete(f"/api/project-chat-sessions/{session_id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert session_id not in app.state.project_chat_sessions


def test_should_return_404_when_deleting_missing_session(
    client: TestClient,
) -> None:
    response = client.delete(f"/api/project-chat-sessions/{uuid4()}")
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

    action_text = '```action\n{"action": "create_task", "title": "SSE Task"}\n```'
    mock_session = _make_mock_session_with_queue(project_id, [action_text])

    app = _make_test_app(store)
    session_id = str(uuid4())
    app.state.project_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/project-chat-sessions/{session_id}/message",
        json={"user_input": "create a task"},
    )
    assert response.status_code == 200

    # Parse SSE events
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    action_events = [e for e in events if e.get("type") == "action_executed"]
    assert len(action_events) == 1
    assert action_events[0]["action"] == "create_task"
    assert action_events[0]["result"]["success"] is True
    assert "SSE Task" in action_events[0]["result"]["message"]


def test_should_persist_modified_text_after_action_replacement(
    store: InMemoryStore,
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))

    action_text = '```action\n{"action": "create_task", "title": "Persist Task"}\n```'
    mock_session = _make_mock_session_with_queue(project_id, [action_text])

    app = _make_test_app(store)
    session_id = str(uuid4())
    app.state.project_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    client.post(
        f"/api/project-chat-sessions/{session_id}/message",
        json={"user_input": "create a task"},
    )

    # Verify the persisted assistant text does not contain raw JSON action block
    session_events = await_or_sync(store.get_events(UUID(session_id), "chat_session"))
    message_events = [
        e for e in session_events if e.event_type == ev.CHAT_SESSION_MESSAGE_ADDED
    ]
    assert len(message_events) == 1
    persisted_text = message_events[0].payload["assistant_text"]
    assert "```action" not in persisted_text
    assert "Persist Task" in persisted_text


def await_or_sync(coro: object) -> object:
    """Run a coroutine synchronously using get_event_loop."""
    return asyncio.get_event_loop().run_until_complete(coro)  # type: ignore[arg-type]
