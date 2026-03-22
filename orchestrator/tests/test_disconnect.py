"""Unit tests for handle_disconnect — no database required."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from core import events as ev
from core.execution_manager import ExecutionManager
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.registry import WorkerConnection, WorkerRegistry
from web.routes.api.ws_worker import _do_disconnect_cleanup, handle_disconnect


def _make_store() -> InMemoryStore:
    return InMemoryStore()


def _fake_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


def _make_worker_conn(
    execution_id: str | None = None,
    worker_id: str | None = None,
) -> WorkerConnection:
    from datetime import UTC, datetime

    return WorkerConnection(
        worker_id=worker_id or str(uuid4()),
        capabilities=[],
        current_execution_id=execution_id,
        websocket=_fake_ws(),
        connected_at=datetime.now(tz=UTC),
    )


async def _seed_running_task(store: InMemoryStore) -> tuple[object, object, str]:
    """Seed a task in in_progress state with a running execution.

    Returns (task_id, execution_id, repo_path).
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

    # Seed execution events directly
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
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )

    return task.id, execution_id, project.local_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_disconnect_idle_worker_is_noop() -> None:
    """Idle worker (no current_execution_id) should result in no events appended."""
    store = _make_store()
    registry = WorkerRegistry()
    conn = _make_worker_conn(execution_id=None)

    initial_event_count = len(store._events)
    await handle_disconnect(conn, store, registry)
    assert len(store._events) == initial_event_count


@pytest.mark.asyncio
async def test_handle_disconnect_records_audit_event() -> None:
    """Disconnect cleanup should append a TASK_WORKER_DISCONNECTED event on the task aggregate."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_running_task(store)

    conn = _make_worker_conn(execution_id=str(execution_id))

    with patch("core.execution_manager.cleanup_task_environment"):
        await _do_disconnect_cleanup(conn, store, registry)

    task_events = await store.get_events(task_id, "task")
    disconnect_events = [
        e for e in task_events if e.event_type == ev.TASK_WORKER_DISCONNECTED
    ]
    assert len(disconnect_events) == 1
    assert disconnect_events[0].payload["execution_id"] == str(execution_id)
    assert disconnect_events[0].payload["worker_id"] == conn.worker_id


@pytest.mark.asyncio
async def test_handle_disconnect_task_returns_to_ready_for_implementation() -> None:
    """Task should be transitioned back to ready_for_implementation after disconnect cleanup."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_running_task(store)

    conn = _make_worker_conn(execution_id=str(execution_id))

    with patch("core.execution_manager.cleanup_task_environment"):
        await _do_disconnect_cleanup(conn, store, registry)

    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.READY_FOR_IMPLEMENTATION


@pytest.mark.asyncio
async def test_handle_disconnect_execution_is_failed() -> None:
    """Execution should be marked as failed after worker disconnect cleanup."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id, _ = await _seed_running_task(store)

    conn = _make_worker_conn(execution_id=str(execution_id))

    with patch("core.execution_manager.cleanup_task_environment"):
        await _do_disconnect_cleanup(conn, store, registry)

    em = ExecutionManager(store, "/fake/path")
    execution = await em.get_current_execution(task_id)
    # After failing, there should be no running execution
    assert execution is None

    # Verify via execution history that it's failed
    history = await em.get_execution_history(task_id)
    assert len(history) == 1
    assert history[0].status == "failed"
    assert history[0].failure_reason == "worker disconnected"


@pytest.mark.asyncio
async def test_handle_disconnect_no_execution_started_event_does_not_raise() -> None:
    """If no EXECUTION_STARTED event exists, handle_disconnect should return cleanly."""
    store = _make_store()
    registry = WorkerRegistry()
    # Use a random execution_id with no events in store
    conn = _make_worker_conn(execution_id=str(uuid4()))

    # Should not raise
    await handle_disconnect(conn, store, registry)
