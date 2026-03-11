"""Unit tests for web.board_builder using InMemoryStore (no DB required)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from core import events as ev
from core.store import InMemoryStore
from web.board_builder import get_task_status, load_board


@pytest.fixture()
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.mark.asyncio
async def test_should_return_deployed_status_for_get_task_status(
    store: InMemoryStore,
) -> None:
    task_id = uuid4()
    project_id = uuid4()

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"title": "T", "status": ev.READY_FOR_SPEC, "project_id": str(project_id)},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_STATUS_CHANGED,
        payload={"from_status": ev.READY_FOR_SPEC, "to_status": ev.DEPLOYED},
    )

    task_events = await store.get_events(task_id, "task")
    cache: dict[UUID, list] = {task_id: task_events}
    status = get_task_status(task_id, cache)

    assert status == ev.DEPLOYED


def test_should_return_none_from_get_task_status_when_task_not_in_cache() -> None:
    task_id = uuid4()
    cache: dict[UUID, list] = {}
    status = get_task_status(task_id, cache)
    assert status is None


@pytest.mark.asyncio
async def test_should_load_board_returning_all_tasks_and_project_by_id_from_load_board(
    store: InMemoryStore,
) -> None:
    project_id = uuid4()
    task_id = uuid4()

    _PROJECTS_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")
    payload = {
        "project_id": str(project_id),
        "name": "Test Project",
        "repo_url": "https://example.com/repo.git",
        "local_path": "/tmp/test",
        "status": "active",
    }
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=_PROJECTS_REGISTRY_ID,
        aggregate_type="projects",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )

    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload={"task_id": str(task_id)},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"title": "Test Task", "status": ev.READY_FOR_SPEC, "project_id": str(project_id)},
    )

    all_tasks, project_by_id, task_events_cache = await load_board(store)

    assert len(all_tasks) == 1
    assert all_tasks[0]["title"] == "Test Task"
    assert project_id in project_by_id
    assert task_id in task_events_cache
