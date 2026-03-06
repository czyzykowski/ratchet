"""Unit tests for TaskStateMachine using InMemoryStore — no database required."""

from __future__ import annotations

import uuid

import pytest

from core import events as ev
from core.state_machine import InvalidTransitionError, TaskStateMachine
from core.store import InMemoryStore


def _make_sm() -> tuple[TaskStateMachine, InMemoryStore]:
    store = InMemoryStore()
    sm = TaskStateMachine(store)
    return sm, store


async def _seed_task(
    store: InMemoryStore, task_id: uuid.UUID, status: str = ev.READY_FOR_SPEC
) -> None:
    """Append a TASK_CREATED event to seed a task with the given initial status."""
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"status": status, "title": "test task"},
    )


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------


async def test_transition_ready_for_spec_to_spec_qa() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.READY_FOR_SPEC)
    event = await sm.transition(task_id, ev.SPEC_QA)
    assert event.event_type == ev.TASK_STATUS_CHANGED
    assert event.payload["from_status"] == ev.READY_FOR_SPEC
    assert event.payload["to_status"] == ev.SPEC_QA
    assert await sm.get_current_status(task_id) == ev.SPEC_QA


async def test_transition_spec_qa_to_ready_for_implementation() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.SPEC_QA)
    event = await sm.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    assert event.payload["to_status"] == ev.READY_FOR_IMPLEMENTATION
    assert await sm.get_current_status(task_id) == ev.READY_FOR_IMPLEMENTATION


async def test_transition_spec_qa_to_blocked() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.SPEC_QA)
    await sm.transition(task_id, ev.BLOCKED)
    assert await sm.get_current_status(task_id) == ev.BLOCKED


async def test_transition_in_progress_to_ready_for_qa() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.IN_PROGRESS)
    await sm.transition(task_id, ev.READY_FOR_QA)
    assert await sm.get_current_status(task_id) == ev.READY_FOR_QA


async def test_transition_in_progress_to_blocked() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.IN_PROGRESS)
    await sm.transition(task_id, ev.BLOCKED)
    assert await sm.get_current_status(task_id) == ev.BLOCKED


async def test_transition_ready_for_deployment_to_deployed() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.READY_FOR_DEPLOYMENT)
    await sm.transition(task_id, ev.DEPLOYED)
    assert await sm.get_current_status(task_id) == ev.DEPLOYED


async def test_transition_blocked_to_ready_for_spec() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.BLOCKED)
    await sm.transition(task_id, ev.READY_FOR_SPEC)
    assert await sm.get_current_status(task_id) == ev.READY_FOR_SPEC


async def test_transition_blocked_to_ready_for_implementation() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.BLOCKED)
    await sm.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    assert await sm.get_current_status(task_id) == ev.READY_FOR_IMPLEMENTATION


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


async def test_invalid_ready_for_spec_to_in_progress() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.READY_FOR_SPEC)
    with pytest.raises(InvalidTransitionError):
        await sm.transition(task_id, ev.IN_PROGRESS)


async def test_invalid_in_progress_to_ready_for_spec() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.IN_PROGRESS)
    with pytest.raises(InvalidTransitionError):
        await sm.transition(task_id, ev.READY_FOR_SPEC)


async def test_invalid_deployed_to_in_progress() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.DEPLOYED)
    with pytest.raises(InvalidTransitionError):
        await sm.transition(task_id, ev.IN_PROGRESS)


async def test_invalid_spec_qa_to_deployed() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.SPEC_QA)
    with pytest.raises(InvalidTransitionError):
        await sm.transition(task_id, ev.DEPLOYED)


async def test_invalid_ready_for_qa_to_in_progress() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.READY_FOR_QA)
    with pytest.raises(InvalidTransitionError):
        await sm.transition(task_id, ev.IN_PROGRESS)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_get_current_status_unknown_task_returns_none() -> None:
    sm, _ = _make_sm()
    result = await sm.get_current_status(uuid.uuid4())
    assert result is None


async def test_transition_unknown_task_raises_invalid_transition() -> None:
    sm, _ = _make_sm()
    with pytest.raises(InvalidTransitionError):
        await sm.transition(uuid.uuid4(), ev.SPEC_QA)


async def test_multiple_sequential_transitions_produce_correct_status() -> None:
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    await _seed_task(store, task_id, ev.READY_FOR_SPEC)

    await sm.transition(task_id, ev.SPEC_QA)
    assert await sm.get_current_status(task_id) == ev.SPEC_QA

    await sm.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    assert await sm.get_current_status(task_id) == ev.READY_FOR_IMPLEMENTATION

    await sm.transition(task_id, ev.IN_PROGRESS)
    assert await sm.get_current_status(task_id) == ev.IN_PROGRESS

    await sm.transition(task_id, ev.READY_FOR_QA)
    assert await sm.get_current_status(task_id) == ev.READY_FOR_QA

    await sm.transition(task_id, ev.READY_FOR_DEPLOYMENT)
    assert await sm.get_current_status(task_id) == ev.READY_FOR_DEPLOYMENT

    await sm.transition(task_id, ev.DEPLOYED)
    assert await sm.get_current_status(task_id) == ev.DEPLOYED
