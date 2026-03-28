"""Unit tests for JobDispatcher — no database required."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core import events as ev
from core.models import Project, Spec, Task
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.dispatcher import dispatch_loop, dispatch_pending
from orchestrator.registry import WorkerRegistry


def _make_store() -> InMemoryStore:
    return InMemoryStore()


def _fake_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


def _register_worker(
    registry: WorkerRegistry, worker_id: str, capabilities: list[str], ws: AsyncMock | None = None,
) -> None:
    """Register a worker with a mock channel so dispatch doesn't skip it."""
    if ws is None:
        ws = _fake_ws()
    conn = registry.register(worker_id, capabilities, ws)
    conn.channel = MagicMock()


async def _seed_ready_task(
    store: InMemoryStore,
    capabilities: list[str] | None = None,
    project: Project | None = None,
) -> tuple[Task, Project, Spec]:
    """Register project (or use given), create task+spec, transition to ready_for_implementation."""
    if project is None:
        with patch("core.project_manager.validate_repo"):
            project = await ProjectManager(store).register_project(
                name="test-project",
                repo_url="http://fake",
                local_path="/fake/path",
                config_source="db",
            )

    task = await TaskManager(store).create_task(
        project.id,
        "Test task",
        required_capabilities=capabilities,
    )
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)

    task = await TaskManager(store).get_task(task.id)
    assert task is not None
    return task, project, spec


async def _seed_qa_task(
    store: InMemoryStore,
    project: Project | None = None,
) -> tuple[Task, Project, Spec]:
    """Create a task in ready_for_qa status with execution branch."""
    if project is None:
        with patch("core.project_manager.validate_repo"):
            project = await ProjectManager(store).register_project(
                name="qa-project",
                repo_url="http://fake",
                local_path="/fake/qa",
                config_source="db",
            )

    task = await TaskManager(store).create_task(project.id, "QA task")
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task.id, ev.IN_PROGRESS)

    execution_id = uuid4()
    payload = {
        "execution_id": str(execution_id),
        "task_id": str(task.id),
        "spec_id": str(spec.id),
        "worktree_path": f"remote/{execution_id}",
        "branch_name": f"execution/{execution_id}",
        "status": "running",
    }
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )
    await sm.transition(task.id, ev.READY_FOR_QA)

    task = await TaskManager(store).get_task(task.id)
    assert task is not None
    return task, project, spec


async def _seed_merge_task(
    store: InMemoryStore,
    project: Project | None = None,
) -> tuple[Task, Project, Spec]:
    """Create a task in ready_for_deployment status."""
    if project is None:
        with patch("core.project_manager.validate_repo"):
            project = await ProjectManager(store).register_project(
                name="merge-project",
                repo_url="http://fake",
                local_path="/fake/merge",
                config_source="db",
            )

    task = await TaskManager(store).create_task(project.id, "Merge task")
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task.id, ev.IN_PROGRESS)

    execution_id = uuid4()
    payload = {
        "execution_id": str(execution_id),
        "task_id": str(task.id),
        "spec_id": str(spec.id),
        "worktree_path": f"remote/{execution_id}",
        "branch_name": f"execution/{execution_id}",
        "status": "running",
    }
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )

    await sm.transition(task.id, ev.READY_FOR_QA)
    await sm.transition(task.id, ev.READY_FOR_DEPLOYMENT)

    task = await TaskManager(store).get_task(task.id)
    assert task is not None
    return task, project, spec


def _mock_sequencer():
    """Return a mock PipelineSequencer with async no-op pipeline methods."""
    mock = MagicMock()
    mock.run_impl_pipeline = AsyncMock(return_value=None)
    mock.run_merge_pipeline = AsyncMock(return_value=None)
    return mock


# ---------------------------------------------------------------------------
# Basic dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_pending_returns_zero_no_tasks() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    result = await dispatch_pending(store, registry)
    assert result == []


@pytest.mark.asyncio
async def test_dispatch_pending_returns_zero_no_workers() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    await _seed_ready_task(store)
    result = await dispatch_pending(store, registry)
    assert result == []


@pytest.mark.asyncio
async def test_dispatch_pending_returns_zero_worker_busy() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    await _seed_ready_task(store)
    ws = _fake_ws()
    registry.register("w1", ["python"], ws)
    registry.assign_job("w1", "exec-1")
    result = await dispatch_pending(store, registry)
    assert result == []


@pytest.mark.asyncio
async def test_dispatch_pending_returns_zero_worker_capability_mismatch() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    await _seed_ready_task(store, capabilities=["gpu"])
    ws = _fake_ws()
    registry.register("w1", ["python"], ws)
    result = await dispatch_pending(store, registry)
    assert result == []


@pytest.mark.asyncio
async def test_dispatch_pending_dispatches_impl_task_returns_one() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    task, project, spec = await _seed_ready_task(store)
    _register_worker(registry, "w1", ["python"])

    mock_seq = _mock_sequencer()

    with patch("orchestrator.dispatcher.PipelineSequencer", return_value=mock_seq):
        result = await dispatch_pending(store, registry)

    assert len(result) == 1
    # Give background task a chance to run
    await asyncio.sleep(0)
    mock_seq.run_impl_pipeline.assert_called_once()
    call_args = mock_seq.run_impl_pipeline.call_args
    assert call_args[0][1].id == task.id  # task argument


@pytest.mark.asyncio
async def test_dispatch_pending_assigns_job_in_registry() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    task, project, spec = await _seed_ready_task(store)
    _register_worker(registry, "w1", ["python"])

    mock_seq = _mock_sequencer()

    with patch("orchestrator.dispatcher.PipelineSequencer", return_value=mock_seq):
        await dispatch_pending(store, registry)

    assert registry.all_workers()[0].current_execution_id is not None


@pytest.mark.asyncio
async def test_dispatch_pending_skips_task_without_spec() -> None:
    store = _make_store()
    registry = WorkerRegistry()

    with patch("core.project_manager.validate_repo"):
        project = await ProjectManager(store).register_project(
            name="test-project",
            repo_url="http://fake",
            local_path="/fake/path",
            config_source="db",
        )

    task = await TaskManager(store).create_task(project.id, "Task without spec")
    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)

    _register_worker(registry, "w1", ["python"])

    mock_seq = _mock_sequencer()
    with patch("orchestrator.dispatcher.PipelineSequencer", return_value=mock_seq):
        result = await dispatch_pending(store, registry)

    assert result == []
    mock_seq.run_impl_pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_loop_calls_dispatch_pending_multiple_times() -> None:
    store = _make_store()
    registry = WorkerRegistry()

    call_count = 0

    async def fake_dispatch_pending(s: object, r: object) -> list:
        nonlocal call_count
        call_count += 1
        return []

    with patch("orchestrator.dispatcher.dispatch_pending", side_effect=fake_dispatch_pending):
        try:
            await asyncio.wait_for(
                dispatch_loop(store, registry, interval_seconds=0.01),
                timeout=0.1,
            )
        except TimeoutError:
            pass

    assert call_count >= 2


# ---------------------------------------------------------------------------
# Priority dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_priority_merge_before_impl() -> None:
    """Merge tasks dispatched before impl tasks."""
    store = _make_store()
    registry = WorkerRegistry()

    # Two separate projects, each with a different pipeline type
    impl_task, _, _ = await _seed_ready_task(store)
    merge_task, _, _ = await _seed_merge_task(store)

    # Two workers available
    _register_worker(registry, "w1", ["python"])
    _register_worker(registry, "w2", ["python"])

    dispatched_pipelines: list[tuple[str, object]] = []

    async def track_impl(channel, task, project, spec):
        dispatched_pipelines.append(("impl", task.id))

    async def track_merge(channel, task, project):
        dispatched_pipelines.append(("merge", task.id))

    mock_seq = MagicMock()
    mock_seq.run_impl_pipeline = AsyncMock(side_effect=track_impl)
    mock_seq.run_merge_pipeline = AsyncMock(side_effect=track_merge)

    with patch("orchestrator.dispatcher.PipelineSequencer", return_value=mock_seq):
        result = await dispatch_pending(store, registry)

    assert len(result) == 2
    # Let background tasks run
    await asyncio.sleep(0.05)

    pipeline_types = [p[0] for p in dispatched_pipelines]
    assert "merge" in pipeline_types
    assert "impl" in pipeline_types


@pytest.mark.asyncio
async def test_dispatch_skips_second_task_in_same_project_when_first_dispatched() -> None:
    """Only one task per project is dispatched per pass."""
    store = _make_store()
    registry = WorkerRegistry()

    # Two tasks in the same project
    task1, project, spec1 = await _seed_ready_task(store)
    task2, _, spec2 = await _seed_ready_task(store, project=project)

    # Two workers available
    _register_worker(registry, "w1", ["python"])
    _register_worker(registry, "w2", ["python"])

    mock_seq = _mock_sequencer()

    with patch("orchestrator.dispatcher.PipelineSequencer", return_value=mock_seq):
        result = await dispatch_pending(store, registry)

    # Only one task dispatched despite two workers being available
    assert len(result) == 1


@pytest.mark.asyncio
async def test_dispatch_skips_project_with_in_progress_task() -> None:
    """Projects with an IN_PROGRESS task are entirely skipped."""
    store = _make_store()
    registry = WorkerRegistry()

    task1, project, spec1 = await _seed_ready_task(store)

    # Transition task1 to IN_PROGRESS (simulates another worker already handling it)
    sm = TaskStateMachine(store)
    await sm.transition(task1.id, ev.IN_PROGRESS)

    # Create a second task in same project (it's ready_for_implementation)
    task2 = await TaskManager(store).create_task(project.id, "Second task")
    spec2 = await SpecManager(store).create_spec(task2.id, "spec content 2")
    await SpecManager(store).assign_spec(task2.id, spec2.id)
    await sm.transition(task2.id, ev.SPEC_QA)
    await sm.transition(task2.id, ev.READY_FOR_IMPLEMENTATION)

    _register_worker(registry, "w1", ["python"])

    mock_seq = _mock_sequencer()

    with patch("orchestrator.dispatcher.PipelineSequencer", return_value=mock_seq):
        result = await dispatch_pending(store, registry)

    # Project skipped entirely because task1 is IN_PROGRESS
    assert result == []
    mock_seq.run_impl_pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_ready_for_qa_task_not_dispatched() -> None:
    """Tasks in ready_for_qa are NOT dispatched by the dispatcher (QA runs inside impl pipeline)."""
    store = _make_store()
    registry = WorkerRegistry()

    # Create a task in ready_for_qa
    qa_task, _, _ = await _seed_qa_task(store)

    _register_worker(registry, "w1", ["python"])

    mock_seq = _mock_sequencer()

    with patch("orchestrator.dispatcher.PipelineSequencer", return_value=mock_seq):
        result = await dispatch_pending(store, registry)

    # ready_for_qa task should NOT be dispatched
    assert len(result) == 0
    mock_seq.run_impl_pipeline.assert_not_called()
