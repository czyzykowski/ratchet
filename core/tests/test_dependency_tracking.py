"""Unit tests for task dependency tracking using InMemoryStore — no database required."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from core import events as ev
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from worker.runner import _build_task_from_events, get_next_task

FAKE_REPO_PATH = "/fake/repo"


async def _setup_project(store: InMemoryStore):
    """Register a project and return project."""
    pm = ProjectManager(store)
    with patch("core.project_manager.validate_repo"):
        project = await pm.register_project(
            name="test-project",
            repo_url="https://github.com/test/repo",
            local_path=FAKE_REPO_PATH,
        )
    return pm, project


async def _setup_task(
    store: InMemoryStore,
    project_id: uuid.UUID,
    initial_status: str = ev.READY_FOR_SPEC,
    title: str = "Test task",
) -> uuid.UUID:
    """Create a task in the store with project_tasks registry entry."""
    task_id = uuid.uuid4()
    payload = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "title": title,
        "status": initial_status,
        "refinement_count": 0,
    }
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload=payload,
    )
    return task_id


async def _advance_to_ready(store: InMemoryStore, task_id: uuid.UUID) -> None:
    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.SPEC_QA)
    await sm.transition(task_id, ev.READY_FOR_IMPLEMENTATION)


async def _setup_spec(store: InMemoryStore, task_id: uuid.UUID) -> uuid.UUID:
    spec_manager = SpecManager(store)
    spec = await spec_manager.create_spec(task_id, "# Spec\nDo the thing.")
    await spec_manager.assign_spec(task_id, spec.id)
    return spec.id


async def _deploy_task(store: InMemoryStore, task_id: uuid.UUID) -> None:
    """Transition a task all the way to deployed."""
    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.SPEC_QA)
    await sm.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task_id, ev.IN_PROGRESS)
    await sm.transition(task_id, ev.READY_FOR_QA)
    await sm.transition(task_id, ev.READY_FOR_DEPLOYMENT)
    await sm.transition(task_id, ev.DEPLOYED)


# ---------------------------------------------------------------------------
# Event replay populates depends_on
# ---------------------------------------------------------------------------


async def test_depends_on_populated_from_event_replay() -> None:
    """should populate depends_on when TASK_DEPENDENCY_ADDED event is replayed."""
    store = InMemoryStore()
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    dep1 = str(uuid.uuid4())
    dep2 = str(uuid.uuid4())

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"title": "Downstream", "status": ev.READY_FOR_SPEC},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [dep1, dep2]},
    )

    events = await store.get_events(task_id, "task")
    task = _build_task_from_events(task_id, project_id, events)

    assert task is not None
    assert dep1 in task.depends_on
    assert dep2 in task.depends_on


async def test_depends_on_accumulates_across_multiple_events() -> None:
    """should accumulate depends_on across multiple TASK_DEPENDENCY_ADDED events."""
    store = InMemoryStore()
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    dep1 = str(uuid.uuid4())
    dep2 = str(uuid.uuid4())

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"title": "Downstream", "status": ev.READY_FOR_SPEC},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [dep1]},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [dep2]},
    )

    events = await store.get_events(task_id, "task")
    task = _build_task_from_events(task_id, project_id, events)

    assert task is not None
    assert dep1 in task.depends_on
    assert dep2 in task.depends_on


async def test_depends_on_empty_when_no_dependency_events() -> None:
    """should have empty depends_on when no TASK_DEPENDENCY_ADDED event exists."""
    store = InMemoryStore()
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()

    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"title": "Standalone", "status": ev.READY_FOR_SPEC},
    )

    events = await store.get_events(task_id, "task")
    task = _build_task_from_events(task_id, project_id, events)

    assert task is not None
    assert task.depends_on == []


# ---------------------------------------------------------------------------
# Worker skips tasks with unmet deps
# ---------------------------------------------------------------------------


async def test_worker_skips_task_when_dep_not_deployed() -> None:
    """should skip task when dependency is not yet deployed."""
    store = InMemoryStore()
    pm, project = await _setup_project(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    # Create upstream task (not deployed)
    upstream_id = await _setup_task(store, project.id, title="Upstream")

    # Create downstream task with dep on upstream
    downstream_id = await _setup_task(store, project.id, title="Downstream")
    await store.append_event(
        aggregate_id=downstream_id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [str(upstream_id)]},
    )
    await _advance_to_ready(store, downstream_id)
    await _setup_spec(store, downstream_id)

    result = await get_next_task(store, pm, spec_manager, state_machine)

    # Downstream should be skipped; upstream has no spec so also skipped
    assert result is None


async def test_worker_picks_task_when_all_deps_deployed() -> None:
    """should pick task when all dependencies are deployed."""
    store = InMemoryStore()
    pm, project = await _setup_project(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    # Create upstream and deploy it
    upstream_id = await _setup_task(store, project.id, title="Upstream")
    await _deploy_task(store, upstream_id)

    # Create downstream with dep on upstream
    downstream_id = await _setup_task(store, project.id, title="Downstream")
    await store.append_event(
        aggregate_id=downstream_id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [str(upstream_id)]},
    )
    await _advance_to_ready(store, downstream_id)
    await _setup_spec(store, downstream_id)

    result = await get_next_task(store, pm, spec_manager, state_machine)

    assert result is not None
    task, _project, _spec = result
    assert task.id == downstream_id


async def test_worker_skips_downstream_picks_upstream_when_ready() -> None:
    """should pick upstream (ready) over downstream (blocked by dep) when both are ready."""
    store = InMemoryStore()
    pm, project = await _setup_project(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    # Upstream task — ready for implementation with a spec
    upstream_id = await _setup_task(store, project.id, title="Upstream")
    await _advance_to_ready(store, upstream_id)
    await _setup_spec(store, upstream_id)

    # Downstream task — depends on upstream (not yet deployed)
    downstream_id = await _setup_task(store, project.id, title="Downstream")
    await store.append_event(
        aggregate_id=downstream_id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [str(upstream_id)]},
    )
    await _advance_to_ready(store, downstream_id)
    await _setup_spec(store, downstream_id)

    result = await get_next_task(store, pm, spec_manager, state_machine)

    assert result is not None
    task, _project, _spec = result
    assert task.id == upstream_id
