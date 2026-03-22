"""Unit tests for disconnect grace period in ws_worker — no database required."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from core import events as ev
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.registry import WorkerConnection, WorkerRegistry
from web.routes.api.ws_worker import (
    _delayed_disconnect_cleanup,
    _pending_disconnects,
    handle_disconnect,
)

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


async def _seed_running_task(store: InMemoryStore) -> tuple[object, object]:
    """Seed a task in in_progress state with a running execution.

    Returns (task_id, execution_id).
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

    return task.id, execution_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_schedules_delayed_cleanup() -> None:
    """handle_disconnect should schedule a delayed task, not immediately reset the task."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id = await _seed_running_task(store)
    conn = _make_worker_conn(execution_id=str(execution_id))

    _pending_disconnects.clear()

    with patch.dict("os.environ", {"WORKER_RECONNECT_TIMEOUT_SECONDS": "9999"}):
        await handle_disconnect(conn, store, registry)

    # Task should still be in_progress — no immediate reset
    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.IN_PROGRESS

    # A pending cleanup task should have been scheduled
    assert str(execution_id) in _pending_disconnects

    # Cancel the pending task to avoid it running in background
    pending = _pending_disconnects.pop(str(execution_id))
    pending.cancel()
    try:
        await pending
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_disconnect_cleanup_runs_after_timeout() -> None:
    """After the timeout expires with no reconnect, task is reset to ready_for_implementation."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id = await _seed_running_task(store)
    conn = _make_worker_conn(execution_id=str(execution_id))

    _pending_disconnects.clear()

    with patch("core.execution_manager.cleanup_task_environment"):
        await _delayed_disconnect_cleanup(conn, store, registry, timeout=0.0)

    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.READY_FOR_IMPLEMENTATION


@pytest.mark.asyncio
async def test_reconnect_cancels_pending_cleanup() -> None:
    """When a reconnected worker reports the matching execution_id, cleanup is skipped."""
    store = _make_store()
    registry = WorkerRegistry()
    task_id, execution_id = await _seed_running_task(store)
    conn = _make_worker_conn(execution_id=str(execution_id))

    _pending_disconnects.clear()

    with patch(
        "web.routes.api.ws_worker._find_worker_with_execution",
        new=AsyncMock(return_value=True),
    ):
        await _delayed_disconnect_cleanup(conn, store, registry, timeout=0.0)

    # Task should still be in_progress — cleanup was skipped
    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.IN_PROGRESS
