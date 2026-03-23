"""Unit tests for web.action_executor."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from core import events as ev
from core.feature_manager import FeatureManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from web.action_executor import execute_action
from web.action_parser import ParsedAction


def _make_parsed(action: str, **payload: object) -> ParsedAction:
    return ParsedAction(action=action, payload=dict(payload), start=0, end=0)


async def _seed_task(store: InMemoryStore, project_id: object) -> object:
    pid = UUID(str(project_id))
    task = await TaskManager(store).create_task(pid, "Seeded task")
    return task


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def project_id() -> object:
    return uuid4()


@pytest.mark.asyncio
async def test_should_create_task_via_action(
    store: InMemoryStore, project_id: object
) -> None:
    parsed = _make_parsed("create_task", title="New Task")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert result.action == "create_task"
    assert "New Task" in result.message
    assert result.entity_id is not None

    task_id = UUID(result.entity_id)
    task = await TaskManager(store).get_task(task_id)
    assert task is not None
    assert task.title == "New Task"


@pytest.mark.asyncio
async def test_should_create_feature_via_action(
    store: InMemoryStore, project_id: object
) -> None:
    parsed = _make_parsed(
        "create_feature", title="Auth Feature", description="Handles auth flows"
    )
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert result.action == "create_feature"
    assert "Auth Feature" in result.message
    assert result.entity_id is not None

    feature_id = UUID(result.entity_id)
    feature = await FeatureManager(store).get_feature(feature_id)
    assert feature is not None
    assert feature.title == "Auth Feature"
    assert feature.description == "Handles auth flows"


@pytest.mark.asyncio
async def test_should_update_task_title(
    store: InMemoryStore, project_id: object
) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("update_task", task_id=task_id, title="Renamed Task")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert "title" in result.message

    task_events = await store.get_events(UUID(task_id), "task")
    title_events = [e for e in task_events if e.event_type == ev.TASK_TITLE_UPDATED]
    assert len(title_events) == 1
    assert title_events[0].payload["title"] == "Renamed Task"


@pytest.mark.asyncio
async def test_should_update_task_status(
    store: InMemoryStore, project_id: object
) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("update_task", task_id=task_id, status=ev.ABANDONED)
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert "status" in result.message

    task_events = await store.get_events(UUID(task_id), "task")
    status_events = [e for e in task_events if e.event_type == ev.TASK_STATUS_CHANGED]
    assert len(status_events) == 1
    assert status_events[0].payload["to_status"] == ev.ABANDONED


@pytest.mark.asyncio
async def test_should_archive_task(store: InMemoryStore, project_id: object) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("archive_task", task_id=task_id, reason="No longer needed")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert result.action == "archive_task"
    assert result.entity_id == task_id

    sm = TaskStateMachine(store)
    status = await sm.get_current_status(UUID(task_id))
    assert status == ev.ABANDONED


@pytest.mark.asyncio
async def test_should_return_error_for_unknown_action(
    store: InMemoryStore, project_id: object
) -> None:
    parsed = _make_parsed("do_something_weird")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is False
    assert result.error is not None
    assert "Unknown action" in result.error


@pytest.mark.asyncio
async def test_should_return_error_for_invalid_transition(
    store: InMemoryStore, project_id: object
) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    # ready_for_spec cannot go to merged directly
    parsed = _make_parsed("update_task", task_id=task_id, status=ev.DEPLOYED)
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_should_return_error_for_missing_required_fields(
    store: InMemoryStore, project_id: object
) -> None:
    # create_task without title
    parsed = _make_parsed("create_task")
    result = await execute_action(parsed, store, UUID(str(project_id)))
    assert result.success is False
    assert result.error is not None

    # archive_task without task_id
    parsed2 = _make_parsed("archive_task")
    result2 = await execute_action(parsed2, store, UUID(str(project_id)))
    assert result2.success is False
    assert result2.error is not None
