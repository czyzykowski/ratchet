"""Unit tests for /api/bootstrap-chat-sessions endpoints."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

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


# ── Task 1: streaming with register_project action ──────────────────────────


def test_should_stream_response_with_register_project_action(
    store: InMemoryStore, tmp_path: object
) -> None:
    import pathlib

    project_dir = pathlib.Path(str(tmp_path))
    (project_dir / ".git").mkdir()

    action_block = (
        "```action\n"
        '{"action": "register_project", "name": "test-proj",'
        f' "path": "{project_dir}", "config_source": "db"}}\n'
        "```"
    )
    mock_session = _make_mock_session_with_queue([action_block])

    app = _make_test_app(store)
    session_id = str(uuid4())
    app.state.bootstrap_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/bootstrap-chat-sessions/{session_id}/message",
        json={"user_input": "register my project"},
    )
    assert response.status_code == 200

    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    chunk_events = [e for e in events if e.get("type") == "chunk"]
    action_events = [e for e in events if e.get("type") == "action_executed"]
    done_events = [e for e in events if e.get("type") == "done"]

    assert len(chunk_events) >= 1
    assert len(action_events) == 1
    assert len(done_events) == 1

    action_event = action_events[0]
    assert action_event["action"] == "register_project"
    assert action_event["result"]["success"] is True
    assert action_event["result"]["entity_id"] is not None


# ── Task 2: streaming with create_task using project context ─────────────────


def test_should_stream_response_with_create_task_action_using_project_context(
    store: InMemoryStore,
) -> None:
    project_id = uuid4()
    session_id = str(uuid4())

    _run(
        store.append_event(
            aggregate_id=UUID(session_id),
            aggregate_type="chat_session",
            event_type="chat_session.created",
            payload={"session_type": "bootstrap", "context_id": None, "context_type": "bootstrap"},
        )
    )
    _run(
        store.append_event(
            aggregate_id=UUID(session_id),
            aggregate_type="chat_session",
            event_type="chat_session.context_updated",
            payload={"context_id": str(project_id), "context_type": "project"},
        )
    )

    action_block = (
        "```action\n"
        '{"action": "create_task", "title": "Initial setup task"}\n'
        "```"
    )
    mock_session = _make_mock_session_with_queue([action_block])

    app = _make_test_app(store)
    app.state.bootstrap_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/bootstrap-chat-sessions/{session_id}/message",
        json={"user_input": "create a task"},
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


# ── Task 3: streaming with check_task_status action ─────────────────────────


def test_should_stream_response_with_check_task_status_action(
    store: InMemoryStore,
) -> None:
    from core.task_manager import TaskManager

    project_id = uuid4()
    task = _run(TaskManager(store).create_task(project_id, "Bootstrap task"))

    action_block = (
        "```action\n"
        f'{{"action": "check_task_status", "task_id": "{task.id}"}}\n'  # type: ignore[attr-defined]
        "```"
    )
    mock_session = _make_mock_session_with_queue([action_block])

    session_id = str(uuid4())
    app = _make_test_app(store)
    app.state.bootstrap_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/bootstrap-chat-sessions/{session_id}/message",
        json={"user_input": "check task status"},
    )
    assert response.status_code == 200

    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    action_events = [e for e in events if e.get("type") == "action_executed"]
    assert len(action_events) == 1
    assert action_events[0]["action"] == "check_task_status"
    assert action_events[0]["result"]["success"] is True
    assert "ready_for_spec" in action_events[0]["result"]["message"]


# ── Task 4: context update event after register_project ─────────────────────


def test_should_update_session_context_after_register_project_action(
    store: InMemoryStore, tmp_path: object
) -> None:
    import pathlib

    from core import events as ev

    project_dir = pathlib.Path(str(tmp_path))
    (project_dir / ".git").mkdir()

    session_id = str(uuid4())
    _run(
        store.append_event(
            aggregate_id=UUID(session_id),
            aggregate_type="chat_session",
            event_type=ev.CHAT_SESSION_CREATED,
            payload={"session_type": "bootstrap", "context_id": None, "context_type": "bootstrap"},
        )
    )

    action_block = (
        "```action\n"
        '{"action": "register_project", "name": "ctx-proj",'
        f' "path": "{project_dir}", "config_source": "db"}}\n'
        "```"
    )
    mock_session = _make_mock_session_with_queue([action_block])

    app = _make_test_app(store)
    app.state.bootstrap_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/bootstrap-chat-sessions/{session_id}/message",
        json={"user_input": "register project"},
    )
    assert response.status_code == 200
    # Consume stream
    _ = response.text

    session_events = _run(store.get_events(UUID(session_id), "chat_session"))
    context_update_events = [
        e for e in session_events if e.event_type == ev.CHAT_SESSION_CONTEXT_UPDATED  # type: ignore[attr-defined]
    ]
    assert len(context_update_events) == 1
    assert context_update_events[0].payload["context_type"] == "project"
    assert context_update_events[0].payload["context_id"] is not None


# ── Task 5: session listing with and without linked projects ─────────────────


def test_should_list_bootstrap_sessions_with_linked_project(
    store: InMemoryStore,
) -> None:
    session_id_linked = uuid4()
    session_id_unlinked = uuid4()
    sessions = [
        ChatSessionSummary(id=session_id_linked, created_at=datetime.now(tz=UTC)),
        ChatSessionSummary(id=session_id_unlinked, created_at=datetime.now(tz=UTC)),
    ]

    app = _make_test_app(store)
    client = TestClient(app)

    with patch(
        "web.routes.api.bootstrap_chat.get_bootstrap_sessions",
        new=AsyncMock(return_value=sessions),
    ):
        response = client.get("/api/bootstrap-chat-sessions")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    returned_ids = {item["session_id"] for item in data}
    assert str(session_id_linked) in returned_ids
    assert str(session_id_unlinked) in returned_ids


# ── Task 6: session recovery after server restart ────────────────────────────


def test_should_recover_session_from_db_after_restart(
    store: InMemoryStore,
) -> None:
    from core import events as ev

    session_id = str(uuid4())

    _run(
        store.append_event(
            aggregate_id=UUID(session_id),
            aggregate_type="chat_session",
            event_type=ev.CHAT_SESSION_CREATED,
            payload={"session_type": "bootstrap", "context_id": None, "context_type": "bootstrap"},
        )
    )
    _run(
        store.append_event(
            aggregate_id=UUID(session_id),
            aggregate_type="chat_session",
            event_type=ev.CHAT_SESSION_MESSAGE_ADDED,
            payload={
                "user_input": "hello",
                "assistant_text": "Hi there!",
                "image_id": None,
                "image_media_type": None,
            },
        )
    )

    recovered_history: list[object] = []
    mock_session = _make_mock_session_with_queue(["recovery response"])

    def _mock_spec_repl_session(**kwargs: object) -> MagicMock:
        recovered_history.extend(kwargs.get("history", []))  # type: ignore[arg-type]
        return mock_session

    chat_session = ChatSession(
        id=UUID(session_id),
        session_type="bootstrap",
        context_id=None,
        context_type="bootstrap",
        created_at=datetime.now(tz=UTC),
        messages=[("hello", "Hi there!", None, None)],
    )

    app = _make_test_app(store)
    # Deliberately do NOT add session to bootstrap_chat_sessions (simulates restart)
    client = TestClient(app)

    with patch(
        "web.routes.api.bootstrap_chat.get_chat_session_by_id",
        new=AsyncMock(return_value=chat_session),
    ), patch(
        "web.routes.api.bootstrap_chat.SpecReplSession",
        side_effect=_mock_spec_repl_session,
    ):
        response = client.post(
            f"/api/bootstrap-chat-sessions/{session_id}/message",
            json={"user_input": "are you there?"},
        )

    assert response.status_code == 200
    # Session should be cached after recovery
    assert session_id in app.state.bootstrap_chat_sessions
    # History should have been passed to SpecReplSession
    assert len(recovered_history) == 1
    assert recovered_history[0][0] == "hello"  # type: ignore[index]


# ── Task 7: error cases ──────────────────────────────────────────────────────


def test_should_handle_create_task_before_project_registered(
    store: InMemoryStore,
) -> None:
    action_block = (
        "```action\n"
        '{"action": "create_task", "title": "Premature task"}\n'
        "```"
    )
    mock_session = _make_mock_session_with_queue([action_block])

    session_id = str(uuid4())
    app = _make_test_app(store)
    app.state.bootstrap_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/bootstrap-chat-sessions/{session_id}/message",
        json={"user_input": "create a task"},
    )
    assert response.status_code == 200

    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    action_events = [e for e in events if e.get("type") == "action_executed"]
    assert len(action_events) == 1
    assert action_events[0]["result"]["success"] is False
    assert "register a project first" in action_events[0]["result"]["error"]


def test_should_handle_register_project_with_invalid_path(
    store: InMemoryStore,
) -> None:
    action_block = (
        "```action\n"
        '{"action": "register_project", "name": "bad-proj", "path": "/nonexistent/path/xyz"}\n'
        "```"
    )
    mock_session = _make_mock_session_with_queue([action_block])

    session_id = str(uuid4())
    app = _make_test_app(store)
    app.state.bootstrap_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/bootstrap-chat-sessions/{session_id}/message",
        json={"user_input": "register project"},
    )
    assert response.status_code == 200

    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    action_events = [e for e in events if e.get("type") == "action_executed"]
    assert len(action_events) == 1
    assert action_events[0]["result"]["success"] is False


# ── Task 8: message persistence after stream completes ──────────────────────


def test_should_persist_message_after_stream_completes(
    store: InMemoryStore,
) -> None:
    from core import events as ev

    mock_session = _make_mock_session_with_queue(["Here is my response."])

    session_id = str(uuid4())
    app = _make_test_app(store)
    app.state.bootstrap_chat_sessions[session_id] = mock_session
    client = TestClient(app)

    response = client.post(
        f"/api/bootstrap-chat-sessions/{session_id}/message",
        json={"user_input": "hello world"},
    )
    assert response.status_code == 200
    # Consume the full stream
    _ = response.text

    session_events = _run(store.get_events(UUID(session_id), "chat_session"))
    message_events = [
        e for e in session_events if e.event_type == ev.CHAT_SESSION_MESSAGE_ADDED  # type: ignore[attr-defined]
    ]
    assert len(message_events) == 1
    assert message_events[0].payload["user_input"] == "hello world"
    assert "Here is my response." in message_events[0].payload["assistant_text"]
