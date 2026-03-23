"""Tests for dispatch_pending bug fixes."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from core import events as ev
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from orchestrator.dispatcher import dispatch_pending
from orchestrator.registry import WorkerRegistry

FAKE_REPO_PATH = "/fake/repo"


async def _setup_project(store: InMemoryStore):
    pm = ProjectManager(store)
    with patch("core.project_manager.validate_repo"):
        project = await pm.register_project(
            name="test-project",
            repo_url="https://github.com/test/repo",
            local_path=FAKE_REPO_PATH,
        )
    return pm, project


async def _setup_task(store: InMemoryStore, project_id: uuid.UUID, title: str = "Test task"):
    task_id = uuid.uuid4()
    payload = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "title": title,
        "status": ev.READY_FOR_SPEC,
        "refinement_count": 0,
    }
    await store.append_event(
        aggregate_id=task_id, aggregate_type="task",
        event_type=ev.TASK_CREATED, payload=payload,
    )
    await store.append_event(
        aggregate_id=project_id, aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED, payload=payload,
    )
    return task_id


async def _advance_to_ready(store: InMemoryStore, task_id: uuid.UUID):
    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.SPEC_QA)
    await sm.transition(task_id, ev.READY_FOR_IMPLEMENTATION)


async def _setup_spec(store: InMemoryStore, task_id: uuid.UUID):
    sm = SpecManager(store)
    spec = await sm.create_spec(task_id, "# Spec\nDo the thing.")
    await sm.assign_spec(task_id, spec.id)
    return spec.id


async def _advance_to_qa(store: InMemoryStore, task_id: uuid.UUID):
    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.IN_PROGRESS)
    await sm.transition(task_id, ev.READY_FOR_QA)


def _make_registry_with_worker(worker_id: str = "worker-1", caps: list[str] | None = None):
    registry = WorkerRegistry()
    ws = AsyncMock()
    conn = registry.register(worker_id, caps or [], ws)
    mock_channel = AsyncMock()
    mock_channel.worker_id = worker_id
    conn.channel = mock_channel
    return registry


async def _add_dependency(store: InMemoryStore, task_id: uuid.UUID, dep_ids: list[uuid.UUID]):
    await store.append_event(
        aggregate_id=task_id, aggregate_type="task",
        event_type=ev.TASK_DEPENDENCY_ADDED,
        payload={"depends_on": [str(d) for d in dep_ids]},
    )


# ---------------------------------------------------------------------------
# Bug 4: Task should be IN_PROGRESS after dispatch_pending returns
# ---------------------------------------------------------------------------


async def test_task_transitions_to_in_progress_before_dispatch_returns():
    """dispatch_pending should transition the task to IN_PROGRESS synchronously,
    not in the async pipeline task. This prevents duplicate dispatch on the next cycle."""
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    registry = _make_registry_with_worker()

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        new_callable=AsyncMock,
    ):
        count = await dispatch_pending(store, registry)

    assert count == 1
    # The task MUST be in_progress after dispatch_pending returns
    sm = TaskStateMachine(store)
    status = await sm.get_current_status(task_id)
    assert status == ev.IN_PROGRESS


# ---------------------------------------------------------------------------
# Bug 3: Reservation should use execution_id for orphan recovery matching
# ---------------------------------------------------------------------------


async def test_worker_job_matches_execution_id_from_assignment_event():
    """The worker's current_execution_id should match the execution_id in the
    TASK_ASSIGNED_TO_WORKER event, so orphan recovery can correlate them."""
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    registry = _make_registry_with_worker()

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        new_callable=AsyncMock,
    ):
        await dispatch_pending(store, registry)

    # Get the execution_id from the TASK_ASSIGNED_TO_WORKER event
    task_events = await store.get_events(task_id, "task")
    assigned_events = [e for e in task_events if e.event_type == ev.TASK_ASSIGNED_TO_WORKER]
    assert len(assigned_events) == 1
    event_exec_id = assigned_events[0].payload["execution_id"]

    # The worker's current_execution_id should match
    worker = registry.all_workers()[0]
    assert worker.current_execution_id == event_exec_id


# ---------------------------------------------------------------------------
# Bug 8: Tasks with unmet dependencies should not be dispatched
# ---------------------------------------------------------------------------


async def test_skips_task_with_unmet_dependencies():
    store = InMemoryStore()
    _, project = await _setup_project(store)

    dep_id = await _setup_task(store, project.id, title="Dependency")
    await _advance_to_ready(store, dep_id)  # not deployed

    task_id = await _setup_task(store, project.id, title="Dependent")
    await _advance_to_ready(store, task_id)
    await _setup_spec(store, task_id)
    await _add_dependency(store, task_id, [dep_id])

    registry = _make_registry_with_worker()

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        new_callable=AsyncMock,
    ):
        count = await dispatch_pending(store, registry)

    assert count == 0


# ---------------------------------------------------------------------------
# Bug 7: Orphan recovery should restore correct pre-pipeline state
# ---------------------------------------------------------------------------


async def test_orphan_recovery_resets_impl_task_to_ready_for_implementation():
    """An in_progress task from an impl pipeline should recover to ready_for_implementation."""
    from orchestrator.dispatcher import _recover_orphaned_tasks

    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_to_ready(store, task_id)

    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.IN_PROGRESS)

    registry = WorkerRegistry()  # empty — no active workers
    await _recover_orphaned_tasks(store, registry)

    status = await sm.get_current_status(task_id)
    assert status == ev.READY_FOR_IMPLEMENTATION


async def test_orphan_recovery_skips_task_with_active_worker():
    """An in_progress task whose worker is still connected should not be recovered."""
    from orchestrator.dispatcher import _recover_orphaned_tasks

    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_to_ready(store, task_id)

    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.IN_PROGRESS)

    # Record assignment
    exec_id = "exec-active"
    await store.append_event(
        aggregate_id=task_id, aggregate_type="task",
        event_type=ev.TASK_ASSIGNED_TO_WORKER,
        payload={"worker_id": "w1", "execution_id": exec_id},
    )

    # Worker is connected and has the execution
    registry = _make_registry_with_worker("w1")
    registry.assign_job("w1", exec_id)

    await _recover_orphaned_tasks(store, registry)

    status = await sm.get_current_status(task_id)
    assert status == ev.IN_PROGRESS  # NOT recovered — worker is active
