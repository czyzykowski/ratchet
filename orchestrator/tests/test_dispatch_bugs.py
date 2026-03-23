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


# ---------------------------------------------------------------------------
# Bug 6: Crash recovery should retry (not block) if task wasn't IN_PROGRESS yet
# ---------------------------------------------------------------------------


async def test_crash_recovery_blocks_task_that_was_in_progress():
    """Pipeline crash after IN_PROGRESS should transition to BLOCKED."""
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    registry = _make_registry_with_worker()

    # Pipeline that crashes after dispatch (task already IN_PROGRESS)
    async def crashing_pipeline(*_a: object, **_kw: object) -> None:
        raise RuntimeError("boom")

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        side_effect=crashing_pipeline,
    ):
        await dispatch_pending(store, registry)

    # Let the async crash handler run
    import asyncio

    await asyncio.sleep(0.1)

    sm = TaskStateMachine(store)
    status = await sm.get_current_status(task_id)
    assert status == ev.BLOCKED


# ---------------------------------------------------------------------------
# Bug 1: dispatch_pending should not dispatch a task whose status changed
#         between candidate collection and _start_pipeline
# ---------------------------------------------------------------------------


async def test_does_not_dispatch_same_project_twice_in_one_cycle():
    """Only one task per project should be dispatched per dispatch_pending call."""
    store = InMemoryStore()
    _, project = await _setup_project(store)

    task1 = await _setup_task(store, project.id, title="Task 1")
    await _advance_to_ready(store, task1)
    await _setup_spec(store, task1)

    task2 = await _setup_task(store, project.id, title="Task 2")
    await _advance_to_ready(store, task2)
    await _setup_spec(store, task2)

    # Two workers available
    registry = WorkerRegistry()
    for wid in ["w1", "w2"]:
        ws = AsyncMock()
        conn = registry.register(wid, [], ws)
        ch = AsyncMock()
        ch.worker_id = wid
        conn.channel = ch

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        new_callable=AsyncMock,
    ):
        count = await dispatch_pending(store, registry)

    # Only one task dispatched (one per project)
    assert count == 1


# ---------------------------------------------------------------------------
# Bug 2: Worker capacity — should not dispatch to a busy worker
# ---------------------------------------------------------------------------


async def test_does_not_dispatch_to_busy_worker():
    """A worker with current_execution_id set should not receive new tasks."""
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    registry = _make_registry_with_worker()
    # Mark the only worker as busy
    registry.assign_job("worker-1", "some-existing-job")

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        new_callable=AsyncMock,
    ):
        count = await dispatch_pending(store, registry)

    assert count == 0  # no available workers


async def test_second_dispatch_cycle_skips_in_progress_project():
    """On the next dispatch_pending call, projects with IN_PROGRESS tasks are skipped."""
    store = InMemoryStore()
    _, project = await _setup_project(store)

    task1 = await _setup_task(store, project.id, title="Task 1")
    await _advance_to_ready(store, task1)
    await _setup_spec(store, task1)

    task2 = await _setup_task(store, project.id, title="Task 2")
    await _advance_to_ready(store, task2)
    await _setup_spec(store, task2)

    registry = _make_registry_with_worker()

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        new_callable=AsyncMock,
    ):
        # First cycle: dispatches task1
        count1 = await dispatch_pending(store, registry)
        assert count1 == 1

        # Clear worker so it's "available" again
        registry.clear_job("worker-1")

        # Second cycle: task1 is IN_PROGRESS, project skipped entirely
        count2 = await dispatch_pending(store, registry)
        assert count2 == 0


# ---------------------------------------------------------------------------
# Bug 5: WAITING_FOR_INPUT tasks with answered questions should be dispatched
# ---------------------------------------------------------------------------


async def test_dispatches_waiting_for_input_task_with_answered_question():
    """A task in WAITING_FOR_INPUT whose question has been answered should be
    picked up by dispatch_pending (as an impl pipeline to resume)."""
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.IN_PROGRESS)
    await sm.transition(task_id, ev.WAITING_FOR_INPUT, extra_payload={
        "execution_id": str(uuid.uuid4()),
    })

    # Record a question and answer
    await store.append_event(
        aggregate_id=task_id, aggregate_type="task",
        event_type=ev.TASK_INPUT_REQUESTED,
        payload={
            "question": "What database?",
            "execution_id": str(uuid.uuid4()),
            "question_index": 0,
        },
    )
    await store.append_event(
        aggregate_id=task_id, aggregate_type="task",
        event_type=ev.TASK_INPUT_PROVIDED,
        payload={"answer": "PostgreSQL", "question_index": 0, "answered_by": "cli"},
    )

    registry = _make_registry_with_worker()

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        new_callable=AsyncMock,
    ):
        count = await dispatch_pending(store, registry)

    assert count == 1


async def test_skips_waiting_for_input_task_with_unanswered_question():
    """A task in WAITING_FOR_INPUT with a pending question should NOT be dispatched."""
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.IN_PROGRESS)
    await sm.transition(task_id, ev.WAITING_FOR_INPUT, extra_payload={
        "execution_id": str(uuid.uuid4()),
    })

    # Record a question but NO answer
    await store.append_event(
        aggregate_id=task_id, aggregate_type="task",
        event_type=ev.TASK_INPUT_REQUESTED,
        payload={
            "question": "What database?",
            "execution_id": str(uuid.uuid4()),
            "question_index": 0,
        },
    )

    registry = _make_registry_with_worker()

    with patch(
        "orchestrator.sequencer.PipelineSequencer.run_impl_pipeline",
        new_callable=AsyncMock,
    ):
        count = await dispatch_pending(store, registry)

    assert count == 0
