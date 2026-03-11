"""Unit tests for core.task_manager using InMemoryStore (no DB required)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from core import events as ev
from core.store import InMemoryStore
from core.task_manager import TaskManager


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def manager(store: InMemoryStore) -> TaskManager:
    return TaskManager(store)


@pytest.mark.asyncio
async def test_create_task_returns_ready_for_spec_task(manager: TaskManager) -> None:
    project_id = uuid4()
    task = await manager.create_task(project_id, "My Task")
    assert task.title == "My Task"
    assert task.status == ev.READY_FOR_SPEC
    assert task.project_id == project_id
    assert task.refinement_count == 0
    assert task.depends_on == []
    assert task.current_spec_id is None


@pytest.mark.asyncio
async def test_create_task_with_dependencies(manager: TaskManager) -> None:
    project_id = uuid4()
    dep1 = str(uuid4())
    dep2 = str(uuid4())
    task = await manager.create_task(project_id, "Task with deps", depends_on=[dep1, dep2])
    assert dep1 in task.depends_on
    assert dep2 in task.depends_on


@pytest.mark.asyncio
async def test_get_task_returns_none_for_unknown_id(manager: TaskManager) -> None:
    result = await manager.get_task(uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_task_replays_status_change(
    store: InMemoryStore, manager: TaskManager
) -> None:
    project_id = uuid4()
    task = await manager.create_task(project_id, "Status Task")
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task",
        event_type=ev.TASK_STATUS_CHANGED,
        payload={"from_status": ev.READY_FOR_SPEC, "to_status": ev.IN_PROGRESS},
    )
    result = await manager.get_task(task.id)
    assert result is not None
    assert result.status == ev.IN_PROGRESS


@pytest.mark.asyncio
async def test_get_task_replays_spec_assigned_increments_refinement_count(
    store: InMemoryStore, manager: TaskManager
) -> None:
    project_id = uuid4()
    task = await manager.create_task(project_id, "Spec Task")
    spec_id = uuid4()
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task",
        event_type=ev.TASK_SPEC_ASSIGNED,
        payload={"spec_id": str(spec_id), "previous_spec_id": None},
    )
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task",
        event_type=ev.TASK_SPEC_ASSIGNED,
        payload={"spec_id": str(uuid4()), "previous_spec_id": str(spec_id)},
    )
    result = await manager.get_task(task.id)
    assert result is not None
    assert result.refinement_count == 2
    assert result.current_spec_id is not None


@pytest.mark.asyncio
async def test_get_task_replays_title_change(
    store: InMemoryStore, manager: TaskManager
) -> None:
    project_id = uuid4()
    task = await manager.create_task(project_id, "Old Title")
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task",
        event_type=ev.TASK_TITLE_CHANGED,
        payload={"title": "New Title"},
    )
    result = await manager.get_task(task.id)
    assert result is not None
    assert result.title == "New Title"


@pytest.mark.asyncio
async def test_get_task_replays_dependency_added(
    store: InMemoryStore, manager: TaskManager
) -> None:
    project_id = uuid4()
    task = await manager.create_task(project_id, "Dep Task")
    dep1 = str(uuid4())
    dep2 = str(uuid4())
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [dep1, dep2]},
    )
    result = await manager.get_task(task.id)
    assert result is not None
    assert dep1 in result.depends_on
    assert dep2 in result.depends_on


@pytest.mark.asyncio
async def test_list_tasks_by_project_returns_all_tasks(manager: TaskManager) -> None:
    project_id = uuid4()
    other_project_id = uuid4()
    task1 = await manager.create_task(project_id, "Task 1")
    task2 = await manager.create_task(project_id, "Task 2")
    await manager.create_task(other_project_id, "Other Project Task")

    tasks = await manager.list_tasks_by_project(project_id)
    task_ids = {t.id for t in tasks}
    assert task1.id in task_ids
    assert task2.id in task_ids
    assert len(tasks) == 2
