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
async def test_create_task_default_required_capabilities(manager: TaskManager) -> None:
    project_id = uuid4()
    task = await manager.create_task(project_id, "Default Capabilities Task")
    assert task.required_capabilities == []


@pytest.mark.asyncio
async def test_create_task_with_required_capabilities(manager: TaskManager) -> None:
    project_id = uuid4()
    task = await manager.create_task(
        project_id, "GPU Task", required_capabilities=["gpu", "os:linux"]
    )
    assert task.required_capabilities == ["gpu", "os:linux"]
    replayed = await manager.get_task(task.id)
    assert replayed is not None
    assert replayed.required_capabilities == ["gpu", "os:linux"]


@pytest.mark.asyncio
async def test_create_task_merges_project_and_task_capabilities(manager: TaskManager) -> None:
    """should union project_capabilities and required_capabilities without duplicates"""
    project_id = uuid4()
    task = await manager.create_task(
        project_id,
        "Merged Caps",
        required_capabilities=["gpu", "docker"],
        project_capabilities=["osx", "gpu"],
    )
    # union: osx + gpu + docker (gpu deduplicated)
    assert set(task.required_capabilities) == {"osx", "gpu", "docker"}
    replayed = await manager.get_task(task.id)
    assert replayed is not None
    assert set(replayed.required_capabilities) == {"osx", "gpu", "docker"}


@pytest.mark.asyncio
async def test_create_task_with_only_project_capabilities(manager: TaskManager) -> None:
    """should inherit project capabilities when task has none of its own"""
    project_id = uuid4()
    task = await manager.create_task(
        project_id, "Inherited", project_capabilities=["osx", "windows"]
    )
    assert set(task.required_capabilities) == {"osx", "windows"}


@pytest.mark.asyncio
async def test_update_task_capabilities_overwrites(
    store: InMemoryStore, manager: TaskManager
) -> None:
    """should overwrite required_capabilities on task via TASK_CAPABILITIES_UPDATED event"""
    project_id = uuid4()
    task = await manager.create_task(project_id, "Cap Task", required_capabilities=["osx"])
    assert task.required_capabilities == ["osx"]

    updated = await manager.update_task_capabilities(task.id, ["gpu", "docker"])
    assert updated.required_capabilities == ["gpu", "docker"]

    replayed = await manager.get_task(task.id)
    assert replayed is not None
    assert replayed.required_capabilities == ["gpu", "docker"]


@pytest.mark.asyncio
async def test_update_task_capabilities_appends_event(
    store: InMemoryStore, manager: TaskManager
) -> None:
    """should append TASK_CAPABILITIES_UPDATED event to task aggregate"""
    project_id = uuid4()
    task = await manager.create_task(project_id, "Cap Task")

    await manager.update_task_capabilities(task.id, ["linux"])

    task_events = await store.get_events(task.id, "task")
    cap_events = [e for e in task_events if e.event_type == ev.TASK_CAPABILITIES_UPDATED]
    assert len(cap_events) == 1
    assert cap_events[0].payload["required_capabilities"] == ["linux"]


@pytest.mark.asyncio
async def test_update_task_capabilities_raises_for_unknown_task(manager: TaskManager) -> None:
    """should raise ValueError for unknown task_id"""
    with pytest.raises(ValueError, match="task not found"):
        await manager.update_task_capabilities(uuid4(), ["gpu"])


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
