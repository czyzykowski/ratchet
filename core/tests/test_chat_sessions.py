"""Unit tests for chat session event round-trips using InMemoryStore."""

from __future__ import annotations

from uuid import uuid4

import pytest

from core import events as ev
from core.store import InMemoryStore


@pytest.mark.asyncio
async def test_chat_session_created_event_round_trip() -> None:
    store = InMemoryStore()
    session_id = uuid4()
    context_id = uuid4()

    event = await store.append_event(
        aggregate_id=session_id,
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_CREATED,
        payload={
            "session_type": "spec",
            "context_id": str(context_id),
            "context_type": "task",
        },
    )

    assert event.event_type == ev.CHAT_SESSION_CREATED
    assert event.payload["session_type"] == "spec"
    assert event.payload["context_id"] == str(context_id)
    assert event.payload["context_type"] == "task"

    retrieved = await store.get_events(session_id, "chat_session")
    assert len(retrieved) == 1
    assert retrieved[0].event_type == ev.CHAT_SESSION_CREATED


@pytest.mark.asyncio
async def test_chat_session_message_added_event_round_trip() -> None:
    store = InMemoryStore()
    session_id = uuid4()
    user_input = "What should the spec cover?"
    assistant_text = "The spec should cover auth."

    event = await store.append_event(
        aggregate_id=session_id,
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_MESSAGE_ADDED,
        payload={
            "user_input": user_input,
            "assistant_text": assistant_text,
        },
    )

    assert event.event_type == ev.CHAT_SESSION_MESSAGE_ADDED
    assert event.payload["user_input"] == user_input
    assert event.payload["assistant_text"] == assistant_text

    retrieved = await store.get_events(session_id, "chat_session")
    assert len(retrieved) == 1
    assert retrieved[0].event_type == ev.CHAT_SESSION_MESSAGE_ADDED


@pytest.mark.asyncio
async def test_feature_chat_session_dual_write_to_feature_aggregate() -> None:
    store = InMemoryStore()
    session_id = uuid4()
    feature_id = uuid4()

    await store.append_event(
        aggregate_id=session_id,
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_CREATED,
        payload={
            "session_type": "feature",
            "context_id": str(feature_id),
            "context_type": "feature",
        },
    )

    await store.append_event(
        aggregate_id=feature_id,
        aggregate_type="feature",
        event_type=ev.CHAT_SESSION_CREATED,
        payload={
            "session_id": str(session_id),
            "session_type": "feature",
            "context_id": str(feature_id),
            "context_type": "feature",
        },
    )

    session_events = await store.get_events(session_id, "chat_session")
    assert len(session_events) == 1
    assert session_events[0].event_type == ev.CHAT_SESSION_CREATED

    feature_events = await store.get_events(feature_id, "feature")
    assert len(feature_events) == 1
    assert feature_events[0].event_type == ev.CHAT_SESSION_CREATED

    feature_payload = feature_events[0].payload
    assert feature_payload["session_id"] == str(session_id)
    assert feature_payload["context_id"] == str(feature_id)
    assert feature_payload["context_type"] == "feature"
    assert feature_payload["session_type"] == "feature"


@pytest.mark.asyncio
async def test_get_chat_session_by_context_returns_none_when_no_session_exists() -> None:
    store = InMemoryStore()
    session_id = uuid4()

    events = await store.get_events(session_id, "chat_session")

    created = next(
        (e for e in events if e.event_type == ev.CHAT_SESSION_CREATED),
        None,
    )
    assert created is None
