"""Tests for _handle_execution_completed and _handle_execution_failed."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import events as ev
from core.execution_manager import ExecutionManager
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.registry import WorkerRegistry
from web.routes.api.ws_worker import _handle_execution_completed, _handle_execution_failed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> str:
    repo_path = str(tmp_path)
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    hello = tmp_path / "hello.txt"
    hello.write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    return repo_path


def _make_mock_websocket() -> MagicMock:
    ws = MagicMock()
    ws.send_text = AsyncMock()
    return ws


async def _seed_running_execution(
    store: InMemoryStore, repo_path: str
) -> tuple[object, object]:
    """Register project/task and start an execution. Returns (task_id, execution)."""
    project = await ProjectManager(store).register_project(
        name="test-project",
        repo_url=repo_path,
        local_path=repo_path,
        config_source="db",
    )
    task = await TaskManager(store).create_task(project.id, "Test task")
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task.id, ev.IN_PROGRESS)

    em = ExecutionManager(store, repo_path)
    execution = await em.start_execution(task.id, spec.id)
    return task.id, execution


def _make_valid_patch(worktree_path: str) -> str:
    """Modify hello.txt, capture diff, revert, return patch string."""
    hello = Path(worktree_path) / "hello.txt"
    hello.write_text("hello\nworld\n")
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        check=True,
        text=True,
    )
    patch = result.stdout
    subprocess.run(
        ["git", "checkout", "--", "."],
        cwd=worktree_path,
        check=True,
        capture_output=True,
    )
    return patch


def _make_completed_message(
    worker_id: str, task_id: object, execution_id: object, patch: str
) -> object:
    from core.remote_protocol import ExecutionCompletedMessage

    return ExecutionCompletedMessage(
        type="execution_completed",
        worker_id=worker_id,
        task_id=str(task_id),
        execution_id=str(execution_id),
        patch=patch,
        timestamp_utc="2026-01-01T00:00:00Z",
    )


def _make_failed_message(
    worker_id: str, task_id: object, execution_id: object, failure_reason: str
) -> object:
    from core.remote_protocol import ExecutionFailedMessage

    return ExecutionFailedMessage(
        type="execution_failed",
        worker_id=worker_id,
        task_id=str(task_id),
        execution_id=str(execution_id),
        failure_reason=failure_reason,
        timestamp_utc="2026-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_success_transitions_task_to_ready_for_qa(tmp_path: Path) -> None:
    """Successful patch application transitions task to ready_for_qa."""
    repo_path = _make_repo(tmp_path)
    store = InMemoryStore()
    registry = WorkerRegistry()
    worker_id = "worker-1"
    registry.register(worker_id, [], _make_mock_websocket())

    task_id, execution = await _seed_running_execution(store, repo_path)
    worktree_path = str(tmp_path / ".worktrees" / str(execution.id))
    patch = _make_valid_patch(worktree_path)
    registry.assign_job(worker_id, str(execution.id))

    msg = _make_completed_message(worker_id, task_id, execution.id, patch)
    await _handle_execution_completed(store, registry, worker_id, msg)

    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.READY_FOR_QA


@pytest.mark.asyncio
async def test_patch_success_records_execution_completed(tmp_path: Path) -> None:
    """Successful patch application records EXECUTION_COMPLETED event."""
    repo_path = _make_repo(tmp_path)
    store = InMemoryStore()
    registry = WorkerRegistry()
    worker_id = "worker-1"
    registry.register(worker_id, [], _make_mock_websocket())

    task_id, execution = await _seed_running_execution(store, repo_path)
    worktree_path = str(tmp_path / ".worktrees" / str(execution.id))
    patch = _make_valid_patch(worktree_path)
    registry.assign_job(worker_id, str(execution.id))

    msg = _make_completed_message(worker_id, task_id, execution.id, patch)
    await _handle_execution_completed(store, registry, worker_id, msg)

    execution_events = await store.get_events(execution.id, "execution")
    event_types = [e.event_type for e in execution_events]
    assert ev.EXECUTION_COMPLETED in event_types


@pytest.mark.asyncio
async def test_patch_failure_transitions_task_to_blocked(tmp_path: Path) -> None:
    """Malformed patch causes GitTransferError, transitions task to blocked."""
    repo_path = _make_repo(tmp_path)
    store = InMemoryStore()
    registry = WorkerRegistry()
    worker_id = "worker-1"
    registry.register(worker_id, [], _make_mock_websocket())

    task_id, execution = await _seed_running_execution(store, repo_path)
    registry.assign_job(worker_id, str(execution.id))

    bad_patch = "this is definitely not a valid git patch"
    msg = _make_completed_message(worker_id, task_id, execution.id, bad_patch)
    await _handle_execution_completed(store, registry, worker_id, msg)

    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.BLOCKED


@pytest.mark.asyncio
async def test_patch_failure_records_execution_failed_with_reason(tmp_path: Path) -> None:
    """Malformed patch records EXECUTION_FAILED with reason starting with 'patch apply failed:'."""
    repo_path = _make_repo(tmp_path)
    store = InMemoryStore()
    registry = WorkerRegistry()
    worker_id = "worker-1"
    registry.register(worker_id, [], _make_mock_websocket())

    task_id, execution = await _seed_running_execution(store, repo_path)
    registry.assign_job(worker_id, str(execution.id))

    bad_patch = "this is definitely not a valid git patch"
    msg = _make_completed_message(worker_id, task_id, execution.id, bad_patch)
    await _handle_execution_completed(store, registry, worker_id, msg)

    execution_events = await store.get_events(execution.id, "execution")
    failed_events = [e for e in execution_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed_events) == 1
    assert failed_events[0].payload["failure_reason"].startswith("patch apply failed:")


@pytest.mark.asyncio
async def test_job_failed_message_transitions_task_to_blocked(tmp_path: Path) -> None:
    """ExecutionFailedMessage transitions task to blocked and records EXECUTION_FAILED."""
    repo_path = _make_repo(tmp_path)
    store = InMemoryStore()
    registry = WorkerRegistry()
    worker_id = "worker-1"
    registry.register(worker_id, [], _make_mock_websocket())

    task_id, execution = await _seed_running_execution(store, repo_path)
    registry.assign_job(worker_id, str(execution.id))

    msg = _make_failed_message(worker_id, task_id, execution.id, "worker crashed")
    await _handle_execution_failed(store, registry, worker_id, msg)

    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.BLOCKED

    execution_events = await store.get_events(execution.id, "execution")
    failed_events = [e for e in execution_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed_events) == 1
    assert failed_events[0].payload["failure_reason"] == "worker crashed"


@pytest.mark.asyncio
async def test_registry_cleared_after_completion(tmp_path: Path) -> None:
    """Registry job is cleared after successful patch application."""
    repo_path = _make_repo(tmp_path)
    store = InMemoryStore()
    registry = WorkerRegistry()
    worker_id = "worker-1"
    registry.register(worker_id, [], _make_mock_websocket())

    task_id, execution = await _seed_running_execution(store, repo_path)
    worktree_path = str(tmp_path / ".worktrees" / str(execution.id))
    patch = _make_valid_patch(worktree_path)
    registry.assign_job(worker_id, str(execution.id))

    msg = _make_completed_message(worker_id, task_id, execution.id, patch)
    await _handle_execution_completed(store, registry, worker_id, msg)

    assert registry.all_workers()[0].current_execution_id is None


@pytest.mark.asyncio
async def test_registry_cleared_after_patch_failure(tmp_path: Path) -> None:
    """Registry job is cleared even when patch application fails."""
    repo_path = _make_repo(tmp_path)
    store = InMemoryStore()
    registry = WorkerRegistry()
    worker_id = "worker-1"
    registry.register(worker_id, [], _make_mock_websocket())

    task_id, execution = await _seed_running_execution(store, repo_path)
    registry.assign_job(worker_id, str(execution.id))

    bad_patch = "this is definitely not a valid git patch"
    msg = _make_completed_message(worker_id, task_id, execution.id, bad_patch)
    await _handle_execution_completed(store, registry, worker_id, msg)

    assert registry.all_workers()[0].current_execution_id is None
