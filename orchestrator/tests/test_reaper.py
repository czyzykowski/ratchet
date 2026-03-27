"""Unit tests for _reap_orphaned_executions — no database required."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from core import events as ev
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.dispatcher import _reap_orphaned_executions
from orchestrator.registry import WorkerRegistry


async def _seed_running_execution(
    store: InMemoryStore,
    started_hours_ago: float = 3.0,
) -> tuple[UUID, UUID, UUID]:
    """Create a project, task, spec, and a running execution started N hours ago.

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

    execution_id = uuid4()
    payload = {
        "execution_id": str(execution_id),
        "task_id": str(task.id),
        "spec_id": str(spec.id),
        "worktree_path": f"/fake/path/.worktrees/{execution_id}",
        "branch_name": f"execution/{execution_id}",
        "status": "running",
    }
    started_event = await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )

    # Backdate the started_at by modifying the event in-place (InMemoryStore holds refs)
    old_time = datetime.now(UTC) - timedelta(hours=started_hours_ago)
    for e in store._events:
        if e.id == started_event.id:
            e.occurred_at = old_time

    return task.id, execution_id, spec.id


@pytest.mark.asyncio
async def test_reap_orphaned_execution() -> None:
    """Reaper marks a 3h-old running execution with no worker as failed."""
    store = InMemoryStore()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_running_execution(store, started_hours_ago=3.0)

    count = await _reap_orphaned_executions(store, registry, timeout_hours=2.0)

    assert count == 1

    exec_events = await store.get_events(execution_id, "execution")
    failed = [e for e in exec_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed) == 1
    assert failed[0].payload["failure_reason"] == "execution timed out (worker disconnected)"
    assert failed[0].payload["status"] == "failed"


@pytest.mark.asyncio
async def test_reap_skips_recent_execution() -> None:
    """Reaper does NOT touch executions started less than 2 hours ago."""
    store = InMemoryStore()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_running_execution(store, started_hours_ago=1.0)

    count = await _reap_orphaned_executions(store, registry, timeout_hours=2.0)

    assert count == 0

    exec_events = await store.get_events(execution_id, "execution")
    failed = [e for e in exec_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed) == 0


@pytest.mark.asyncio
async def test_reap_at_startup_with_zero_timeout() -> None:
    """With timeout_hours=0, reaper catches even recent running executions (startup recovery)."""
    store = InMemoryStore()
    registry = WorkerRegistry()
    # Execution started only 30 minutes ago — normally under the 2h threshold
    task_id, execution_id, _ = await _seed_running_execution(store, started_hours_ago=0.5)

    count = await _reap_orphaned_executions(store, registry, timeout_hours=0)

    assert count == 1

    exec_events = await store.get_events(execution_id, "execution")
    failed = [e for e in exec_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed) == 1
    assert "timed out" in failed[0].payload["failure_reason"]


@pytest.mark.asyncio
async def test_reap_skips_execution_with_connected_worker() -> None:
    """Reaper does NOT touch executions whose worker is still connected."""
    from unittest.mock import AsyncMock

    store = InMemoryStore()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_running_execution(store, started_hours_ago=3.0)

    # Register a worker that is working on this execution
    ws = AsyncMock()
    registry.register("active-worker", [], ws)
    registry.assign_job("active-worker", str(execution_id))

    count = await _reap_orphaned_executions(store, registry, timeout_hours=2.0)

    assert count == 0
