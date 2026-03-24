"""Unit tests for /api/bootstrap-chat-sessions endpoints."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.claude_repl import _QueueDone
from core.models import ChatSession, ChatSessionSummary
from core.store import InMemoryStore
from web.routes.api.bootstrap_chat import _build_bootstrap_system_prompt
from web.routes.api.router import api_router


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.state.bootstrap_chat_sessions = {}
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


def _run(coro: object) -> object:
    return asyncio.get_event_loop().run_until_complete(coro)  # type: ignore[arg-type]


def test_should_create_bootstrap_session_without_project_id(
    store: InMemoryStore,
) -> None:
    mock_session = MagicMock()
    app = _make_test_app(store)
    client = TestClient(app)

    with patch(
        "web.routes.api.bootstrap_chat.SpecReplSession",
        return_value=mock_session,
    ):
        response = client.post("/api/bootstrap-chat-sessions", json={})

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["messages"] == []


def test_should_create_bootstrap_session_with_working_directory(
    store: InMemoryStore,
) -> None:
    mock_session = MagicMock()
    app = _make_test_app(store)
    client = TestClient(app)

    with patch(
        "web.routes.api.bootstrap_chat.SpecReplSession",
        return_value=mock_session,
    ) as mock_cls:
        response = client.post(
            "/api/bootstrap-chat-sessions",
            json={"working_directory": "/home/user/myproject"},
        )

    assert response.status_code == 200
    call_kwargs = mock_cls.call_args
    assert call_kwargs.kwargs["cwd"] == "/home/user/myproject"


def test_should_return_404_for_unknown_session(client: TestClient) -> None:
    with patch(
        "web.routes.api.bootstrap_chat.get_chat_session_by_id",
        new=AsyncMock(return_value=None),
    ):
        response = client.get(f"/api/bootstrap-chat-sessions/{uuid4()}")

    assert response.status_code == 404


def test_should_list_bootstrap_sessions(store: InMemoryStore) -> None:
    session_id = uuid4()
    sessions = [ChatSessionSummary(id=session_id, created_at=datetime.now(tz=UTC))]

    app = _make_test_app(store)
    client = TestClient(app)

    with patch(
        "web.routes.api.bootstrap_chat.get_bootstrap_sessions",
        new=AsyncMock(return_value=sessions),
    ):
        response = client.get("/api/bootstrap-chat-sessions")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["session_id"] == str(session_id)


def _make_mock_session_with_queue(chunks: list[object]) -> MagicMock:
    """Create a mock SpecReplSession that returns a pre-filled queue."""
    session = MagicMock()
    session.task_id = str(uuid4())

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


def test_should_send_message_and_stream_response(store: InMemoryStore) -> None:
    mock_session = _make_mock_session_with_queue(["Hello from bootstrap!"])

    app = _make_test_app(store)
    session_id = str(uuid4())
    app.state.bootstrap_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/bootstrap-chat-sessions/{session_id}/message",
        json={"user_input": "I want to build a task tracker"},
    )
    assert response.status_code == 200

    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    chunk_events = [e for e in events if e.get("type") == "chunk"]
    done_events = [e for e in events if e.get("type") == "done"]
    assert len(chunk_events) >= 1
    assert len(done_events) == 1
    assert chunk_events[0]["text"] == "Hello from bootstrap!"


def test_should_delete_session(store: InMemoryStore) -> None:
    app = _make_test_app(store)
    session_id = str(uuid4())
    mock_session = MagicMock()
    mock_session.close = AsyncMock()
    app.state.bootstrap_chat_sessions[session_id] = mock_session

    client = TestClient(app)
    response = client.delete(f"/api/bootstrap-chat-sessions/{session_id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert session_id not in app.state.bootstrap_chat_sessions


def test_should_return_404_when_deleting_missing_session(client: TestClient) -> None:
    response = client.delete(f"/api/bootstrap-chat-sessions/{uuid4()}")
    assert response.status_code == 404


def test_should_return_messages_for_existing_session(client: TestClient) -> None:
    session_id = uuid4()
    chat_session = ChatSession(
        id=session_id,
        session_type="bootstrap",
        context_id=None,
        context_type="bootstrap",
        created_at=datetime.now(tz=UTC),
        messages=[
            ("what should I build?", "Let's explore your idea!", None, None),
        ],
    )

    with patch(
        "web.routes.api.bootstrap_chat.get_chat_session_by_id",
        new=AsyncMock(return_value=chat_session),
    ):
        response = client.get(f"/api/bootstrap-chat-sessions/{session_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_id)
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "what should I build?"
    assert data["messages"][0]["assistant"] == "Let's explore your idea!"


def test_system_prompt_contains_brainstorming_pattern() -> None:
    prompt = _build_bootstrap_system_prompt()

    assert "one question at a time" in prompt.lower() or "ONE question at a time" in prompt
    assert "multiple choice" in prompt.lower()
    assert "approval checkpoint" in prompt.lower() or "approval" in prompt.lower()
    assert "register_project" in prompt
    assert "create_task" in prompt


def test_system_prompt_contains_all_action_types() -> None:
    prompt = _build_bootstrap_system_prompt()

    action_types = [
        "register_project",
        "create_task",
        "create_feature",
        "add_hls",
        "update_task",
        "archive_task",
        "check_task_status",
    ]
    for action_type in action_types:
        assert action_type in prompt, f"Expected action type '{action_type}' in prompt"


def test_system_prompt_has_section_headers() -> None:
    prompt = _build_bootstrap_system_prompt()

    assert "## Role" in prompt
    assert "## Conversation Phases" in prompt
    assert "## Available Actions" in prompt
    assert "## Rules" in prompt
