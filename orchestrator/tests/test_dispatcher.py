"""Unit tests for JobDispatcher — no database required."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from core import events as ev
from core.models import Execution, Project, Spec, Task
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
    return ws


def _fake_execution(task_id: object, spec_id: object) -> Execution:
    return Execution(
        id=uuid4(),
        task_id=task_id,  # type: ignore[arg-type]
        spec_id=spec_id,  # type: ignore[arg-type]
        status="running",
        failure_reason=None,
        branch_name=f"execution/{uuid4()}",
        started_at=datetime.now(tz=UTC),
        completed_at=None,
    )


async def _seed_ready_task(
    store: InMemoryStore,
    capabilities: list[str] | None = None,
) -> tuple[Task, Project, Spec]:
    """Register project, create task+spec, transition to ready_for_implementation."""
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


# ---------------------------------------------------------------------------
# dispatch_pending tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_pending_returns_zero_no_tasks() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    result = await dispatch_pending(store, registry)
    assert result == 0


@pytest.mark.asyncio
async def test_dispatch_pending_returns_zero_no_workers() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    await _seed_ready_task(store)
    result = await dispatch_pending(store, registry)
    assert result == 0


@pytest.mark.asyncio
async def test_dispatch_pending_returns_zero_worker_busy() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    await _seed_ready_task(store)
    ws = _fake_ws()
    registry.register("w1", ["python"], ws)
    registry.assign_job("w1", "exec-1")
    result = await dispatch_pending(store, registry)
    assert result == 0


@pytest.mark.asyncio
async def test_dispatch_pending_returns_zero_worker_capability_mismatch() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    await _seed_ready_task(store, capabilities=["gpu"])
    ws = _fake_ws()
    registry.register("w1", ["python"], ws)
    result = await dispatch_pending(store, registry)
    assert result == 0


@pytest.mark.asyncio
async def test_dispatch_pending_dispatches_task_returns_one() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    task, project, spec = await _seed_ready_task(store)
    ws = _fake_ws()
    registry.register("w1", ["python"], ws)

    fake_exec = _fake_execution(task.id, spec.id)
    with (
        patch("orchestrator.dispatcher.ExecutionManager") as mock_em_cls,
        patch("orchestrator.dispatcher.git_transfer.create_bundle", return_value=b"fake-bundle"),
    ):
        mock_em = AsyncMock()
        mock_em.start_execution = AsyncMock(return_value=fake_exec)
        mock_em_cls.return_value = mock_em

        result = await dispatch_pending(store, registry)

    assert result == 1
    ws.send_text.assert_called_once()
    sent = json.loads(ws.send_text.call_args[0][0])
    assert sent["type"] == "assign_task"
    assert sent["task_id"] == str(task.id)


@pytest.mark.asyncio
async def test_dispatch_pending_assigns_job_in_registry() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    task, project, spec = await _seed_ready_task(store)
    ws = _fake_ws()
    registry.register("w1", ["python"], ws)

    fake_exec = _fake_execution(task.id, spec.id)
    with (
        patch("orchestrator.dispatcher.ExecutionManager") as mock_em_cls,
        patch("orchestrator.dispatcher.git_transfer.create_bundle", return_value=b"fake-bundle"),
    ):
        mock_em = AsyncMock()
        mock_em.start_execution = AsyncMock(return_value=fake_exec)
        mock_em_cls.return_value = mock_em

        await dispatch_pending(store, registry)

    assert registry.all_workers()[0].current_execution_id is not None


@pytest.mark.asyncio
async def test_dispatch_pending_transitions_task_to_in_progress() -> None:
    store = _make_store()
    registry = WorkerRegistry()
    task, project, spec = await _seed_ready_task(store)
    ws = _fake_ws()
    registry.register("w1", ["python"], ws)

    fake_exec = _fake_execution(task.id, spec.id)
    with (
        patch("orchestrator.dispatcher.ExecutionManager") as mock_em_cls,
        patch("orchestrator.dispatcher.git_transfer.create_bundle", return_value=b"fake-bundle"),
    ):
        mock_em = AsyncMock()
        mock_em.start_execution = AsyncMock(return_value=fake_exec)
        mock_em_cls.return_value = mock_em

        await dispatch_pending(store, registry)

    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.IN_PROGRESS


@pytest.mark.asyncio
async def test_dispatch_pending_orders_by_created_at() -> None:
    store = _make_store()
    registry = WorkerRegistry()

    # Create t1 first (older), then t2 (newer)
    t1, project, spec1 = await _seed_ready_task(store)
    t2, _, spec2 = await _seed_ready_task(store)

    ws = _fake_ws()
    registry.register("w1", ["python"], ws)

    async def fake_start_execution(*args: object, **kwargs: object) -> Execution:
        # Determine which task is being dispatched from the call context
        return _fake_execution(args[0], args[1])

    with (
        patch("orchestrator.dispatcher.ExecutionManager") as mock_em_cls,
        patch("orchestrator.dispatcher.git_transfer.create_bundle", return_value=b"fake-bundle"),
    ):
        mock_em = AsyncMock()
        mock_em.start_execution = AsyncMock(side_effect=fake_start_execution)
        mock_em_cls.return_value = mock_em

        result = await dispatch_pending(store, registry)

    # Only one worker → only one task dispatched
    assert result == 1
    ws.send_text.assert_called_once()
    sent = json.loads(ws.send_text.call_args[0][0])
    # t1 was created first → should be dispatched first
    assert sent["task_id"] == str(t1.id)


@pytest.mark.asyncio
async def test_dispatch_pending_skips_task_without_spec() -> None:
    store = _make_store()
    registry = WorkerRegistry()

    # Create project and task, transition to ready_for_implementation WITHOUT assigning spec
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

    ws = _fake_ws()
    registry.register("w1", ["python"], ws)

    result = await dispatch_pending(store, registry)

    assert result == 0
    ws.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_loop_calls_dispatch_pending_multiple_times() -> None:
    store = _make_store()
    registry = WorkerRegistry()

    call_count = 0

    async def fake_dispatch_pending(s: object, r: object) -> int:
        nonlocal call_count
        call_count += 1
        return 0

    with patch("orchestrator.dispatcher.dispatch_pending", side_effect=fake_dispatch_pending):
        try:
            await asyncio.wait_for(
                dispatch_loop(store, registry, interval_seconds=0.01),
                timeout=0.1,
            )
        except TimeoutError:
            pass

    assert call_count >= 2
