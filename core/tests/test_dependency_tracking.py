"""Unit tests for task dependency tracking using InMemoryStore — no database required."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from core import events as ev
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.dispatcher import dispatch_pending
from orchestrator.registry import WorkerRegistry

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


def _make_registry_with_worker() -> WorkerRegistry:
    """Return a registry with one idle worker available."""
    registry = WorkerRegistry()
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock()
    conn = registry.register("worker-1", [], ws)
    # Set a mock channel so dispatch_pending can use it
    mock_channel = AsyncMock()
    mock_channel.worker_id = "worker-1"
    conn.channel = mock_channel
    return registry


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
        payload={"title": "Downstream", "status": ev.READY_FOR_SPEC, "project_id": str(project_id)},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [dep1, dep2]},
    )

    task = await TaskManager(store).get_task(task_id)

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
        payload={"title": "Downstream", "status": ev.READY_FOR_SPEC, "project_id": str(project_id)},
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

    task = await TaskManager(store).get_task(task_id)

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
        payload={"title": "Standalone", "status": ev.READY_FOR_SPEC, "project_id": str(project_id)},
    )

    task = await TaskManager(store).get_task(task_id)

    assert task is not None
    assert task.depends_on == []


# ---------------------------------------------------------------------------
# Orchestrator dispatches tasks with depends_on populated
# ---------------------------------------------------------------------------


async def test_dispatch_pending_dispatches_task_with_depends_on_populated() -> None:
    """should dispatch task and have depends_on field populated from events."""
    store = InMemoryStore()
    _pm, project = await _setup_project(store)

    upstream_id = await _setup_task(store, project.id, title="Upstream")
    await _deploy_task(store, upstream_id)

    downstream_id = await _setup_task(store, project.id, title="Downstream")
    await store.append_event(
        aggregate_id=downstream_id,
        aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [str(upstream_id)]},
    )
    await _advance_to_ready(store, downstream_id)
    await _setup_spec(store, downstream_id)

    registry = _make_registry_with_worker()
    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline", new_callable=AsyncMock
    ):
        count = await dispatch_pending(store, registry)

    assert count == 1

    task = await TaskManager(store).get_task(downstream_id)
    assert task is not None
    assert str(upstream_id) in task.depends_on


async def test_dispatch_pending_returns_zero_when_no_ready_tasks() -> None:
    """should return 0 when no tasks are ready for dispatch."""
    store = InMemoryStore()
    _pm, project = await _setup_project(store)

    # Task exists but not ready for implementation (no spec assigned, not advanced)
    await _setup_task(store, project.id, title="Draft task")

    registry = _make_registry_with_worker()
    count = await dispatch_pending(store, registry)

    assert count == 0


async def test_dispatch_pending_returns_zero_when_no_workers_available() -> None:
    """should return 0 when no workers are available in the registry."""
    store = InMemoryStore()
    _pm, project = await _setup_project(store)

    task_id = await _setup_task(store, project.id, title="Ready task")
    await _advance_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    registry = WorkerRegistry()  # empty — no workers
    count = await dispatch_pending(store, registry)

    assert count == 0
