"""Unit tests for RecoveryManager — no database required."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from core import events as ev
from core.project_manager import ProjectManager
from core.remote_protocol import GetStatusResponse
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.recovery import RecoveryAction, RecoveryManager
from orchestrator.registry import WorkerConnection, WorkerRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store() -> InMemoryStore:
    return InMemoryStore()


def _fake_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


def _make_worker_conn(
    worker_id: str | None = None,
    execution_id: str | None = None,
) -> WorkerConnection:
    from datetime import UTC, datetime

    return WorkerConnection(
        worker_id=worker_id or str(uuid4()),
        capabilities=[],
        current_execution_id=execution_id,
        websocket=_fake_ws(),
        connected_at=datetime.now(tz=UTC),
    )


async def _seed_task_in_progress(
    store: InMemoryStore,
) -> tuple[object, object, object]:
    """Create a project, task, spec, and transition task to in_progress.

    Returns (task_id, execution_id, spec_id).
    """
    with patch("core.project_manager.validate_repo"):
        project = await ProjectManager(store).register_project(
            name="test-project",
            repo_url="http://fake",
            local_path="/fake/path",
            config_source="db",
        )

    task = await TaskManager(store).create_task(project.id, "Test task")
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task.id, ev.IN_PROGRESS)

    # Seed execution events
    execution_id = uuid4()
    payload = {
        "execution_id": str(execution_id),
        "task_id": str(task.id),
        "spec_id": str(spec.id),
        "worktree_path": f"/fake/path/.worktrees/{execution_id}",
        "branch_name": f"execution/{execution_id}",
        "status": "running",
    }
    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task",
        event_type=ev.TASK_ASSIGNED_TO_WORKER,
        payload={"worker_id": "old-worker", "execution_id": str(execution_id)},
    )

    return task.id, execution_id, spec.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_finds_in_progress_tasks() -> None:
    """Recovery returns actions only for in-progress tasks with valid assignment."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_task_in_progress(store)

    manager = RecoveryManager(store, registry, grace_period=0.0)

    # Seed a second, completed task (should be skipped)
    with patch("core.project_manager.validate_repo"):
        project2 = await ProjectManager(store).register_project(
            name="project2",
            repo_url="http://fake2",
            local_path="/fake/path2",
            config_source="db",
        )
    task2 = await TaskManager(store).create_task(project2.id, "Done task")
    spec2 = await SpecManager(store).create_spec(task2.id, "content")
    await SpecManager(store).assign_spec(task2.id, spec2.id)
    sm = TaskStateMachine(store)
    await sm.transition(task2.id, ev.SPEC_QA)
    await sm.transition(task2.id, ev.READY_FOR_IMPLEMENTATION)

    mock_pool = AsyncMock()

    with patch(
        "orchestrator.recovery.get_in_progress_task_ids",
        new=AsyncMock(return_value=[task_id]),
    ):
        actions = await manager.recover_in_progress_tasks(mock_pool)

    assert len(actions) == 1
    assert actions[0].task_id == task_id
    assert actions[0].execution_id == execution_id
    assert actions[0].worker_id == "old-worker"


@pytest.mark.asyncio
async def test_recover_skips_tasks_with_completed_execution() -> None:
    """Recovery skips tasks whose execution already has EXECUTION_COMPLETED event."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_task_in_progress(store)

    # Mark the execution as completed
    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_COMPLETED,
        payload={"execution_id": str(execution_id), "status": "completed"},
    )

    manager = RecoveryManager(store, registry, grace_period=0.0)
    mock_pool = AsyncMock()

    with patch(
        "orchestrator.recovery.get_in_progress_task_ids",
        new=AsyncMock(return_value=[task_id]),
    ):
        actions = await manager.recover_in_progress_tasks(mock_pool)

    assert actions == []


@pytest.mark.asyncio
async def test_reset_orphaned_task_transitions_to_ready() -> None:
    """_reset_orphaned_task marks execution failed and returns task to ready_for_implementation."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_task_in_progress(store)

    manager = RecoveryManager(store, registry, grace_period=0.0)
    await manager._reset_orphaned_task(task_id, execution_id)

    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.READY_FOR_IMPLEMENTATION

    exec_events = await store.get_events(execution_id, "execution")
    failed = [e for e in exec_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed) == 1
    assert failed[0].payload["failure_reason"] == "orchestrator_restart_recovery"


@pytest.mark.asyncio
async def test_wait_and_resolve_resets_after_grace_period() -> None:
    """After grace period with no reconnected workers, task is reset."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_task_in_progress(store)

    manager = RecoveryManager(store, registry, grace_period=0.0)

    action = RecoveryAction(
        task_id=task_id,
        execution_id=execution_id,
        worker_id="old-worker",
    )
    await manager.wait_and_resolve([action])

    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.READY_FOR_IMPLEMENTATION


@pytest.mark.asyncio
async def test_wait_and_resolve_skips_reconnected_worker() -> None:
    """If a worker reconnects and reports the matching execution_id, task stays in_progress."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_task_in_progress(store)

    # Register a worker whose GetStatus returns the matching execution_id
    status_response = GetStatusResponse(
        type="get_status_response",
        request_id="any",
        success=True,
        current_execution_id=str(execution_id),
    )

    mock_ws = AsyncMock()
    mock_ws.send_text = AsyncMock()
    mock_ws.receive_text = AsyncMock(return_value=status_response.model_dump_json())
    registry.register("new-worker", [], mock_ws)

    manager = RecoveryManager(store, registry, grace_period=0.0)

    action = RecoveryAction(
        task_id=task_id,
        execution_id=execution_id,
        worker_id="old-worker",
    )

    # The channel checks request_id matching, so patch send_command directly
    with patch(
        "orchestrator.recovery.WebSocketWorkerChannel.send_command",
        new=AsyncMock(return_value=status_response),
    ):
        await manager.wait_and_resolve([action])

    # Task should still be in_progress — no reset happened
    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.IN_PROGRESS
