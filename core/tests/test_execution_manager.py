"""Unit tests for ExecutionManager using InMemoryStore — no database required."""

from __future__ import annotations

import subprocess
import uuid
from unittest.mock import patch

import pytest

from core import events as ev
from core.execution_manager import ExecutionManager
from core.store import InMemoryStore

REPO_PATH = "/fake/repo"
PATCH_PREPARE = "core.execution_manager.prepare_task_environment"
PATCH_CLEANUP = "core.execution_manager.cleanup_task_environment"


def _make_em() -> tuple[ExecutionManager, InMemoryStore]:
    store = InMemoryStore()
    em = ExecutionManager(store, REPO_PATH)
    return em, store


def _fake_worktree_path(repo_path: str, execution_id: uuid.UUID) -> str:
    return f"{repo_path}/.worktrees/{execution_id}"


# ---------------------------------------------------------------------------
# start_execution — successful
# ---------------------------------------------------------------------------


async def test_start_execution_returns_running_execution() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)

    assert execution.task_id == task_id
    assert execution.spec_id == spec_id
    assert execution.status == "running"
    assert execution.failure_reason is None
    assert execution.completed_at is None
    assert execution.started_at is not None


async def test_start_execution_appends_execution_started_event() -> None:
    em, store = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)

    execution_events = await store.get_events(execution.id, "execution")
    assert len(execution_events) == 1
    assert execution_events[0].event_type == ev.EXECUTION_STARTED
    p = execution_events[0].payload
    assert p["execution_id"] == str(execution.id)
    assert p["task_id"] == str(task_id)
    assert p["spec_id"] == str(spec_id)
    assert p["status"] == "running"
    assert "worktree_path" in p


async def test_start_execution_calls_prepare_with_correct_args() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)

    mock_prepare.assert_called_once_with(REPO_PATH, execution.id)


# ---------------------------------------------------------------------------
# complete_execution
# ---------------------------------------------------------------------------


async def test_complete_execution_appends_completed_event() -> None:
    em, store = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)
        event = await em.complete_execution(execution.id)

    assert event.event_type == ev.EXECUTION_COMPLETED
    assert event.payload["execution_id"] == str(execution.id)
    assert event.payload["status"] == "completed"

    execution_events = await store.get_events(execution.id, "execution")
    event_types = [e.event_type for e in execution_events]
    assert ev.EXECUTION_STARTED in event_types
    assert ev.EXECUTION_COMPLETED in event_types


async def test_complete_execution_calls_cleanup_with_correct_args() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP) as mock_cleanup:
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)
        await em.complete_execution(execution.id)

    mock_cleanup.assert_called_once_with(REPO_PATH, execution.id)


# ---------------------------------------------------------------------------
# fail_execution
# ---------------------------------------------------------------------------


async def test_fail_execution_appends_failed_event_with_reason() -> None:
    em, store = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    reason = "tests failed"

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)
        event = await em.fail_execution(execution.id, reason)

    assert event.event_type == ev.EXECUTION_FAILED
    assert event.payload["execution_id"] == str(execution.id)
    assert event.payload["failure_reason"] == reason
    assert event.payload["status"] == "failed"

    execution_events = await store.get_events(execution.id, "execution")
    event_types = [e.event_type for e in execution_events]
    assert ev.EXECUTION_STARTED in event_types
    assert ev.EXECUTION_FAILED in event_types


async def test_fail_execution_calls_cleanup_with_correct_args() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP) as mock_cleanup:
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)
        await em.fail_execution(execution.id, "reason")

    mock_cleanup.assert_called_once_with(REPO_PATH, execution.id)


# ---------------------------------------------------------------------------
# Environment preparation failure
# ---------------------------------------------------------------------------


async def test_env_prep_failure_raises_environment_error() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = subprocess.CalledProcessError(128, "git")
        with pytest.raises(EnvironmentError):
            await em.start_execution(task_id, spec_id)


async def test_env_prep_failure_appends_execution_failed_event() -> None:
    em, store = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = subprocess.CalledProcessError(128, "git")
        with pytest.raises(EnvironmentError):
            await em.start_execution(task_id, spec_id)

    # Find the EXECUTION_FAILED event in the store — it's stored under execution_id
    all_events = store._events
    failed_events = [e for e in all_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed_events) == 1
    assert "Failed to prepare environment" in failed_events[0].payload["failure_reason"]
    assert failed_events[0].payload["status"] == "failed"


async def test_env_prep_failure_no_execution_started_event() -> None:
    em, store = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = subprocess.CalledProcessError(128, "git")
        with pytest.raises(EnvironmentError):
            await em.start_execution(task_id, spec_id)

    all_events = store._events
    started_events = [e for e in all_events if e.event_type == ev.EXECUTION_STARTED]
    assert len(started_events) == 0


async def test_env_prep_failure_failure_reason_describes_error() -> None:
    em, store = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    underlying = subprocess.CalledProcessError(128, "git worktree add")

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = underlying
        with pytest.raises(EnvironmentError):
            await em.start_execution(task_id, spec_id)

    all_events = store._events
    failed_events = [e for e in all_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed_events) == 1
    reason = failed_events[0].payload["failure_reason"]
    assert len(reason) > 0
    assert "Failed to prepare environment" in reason


# ---------------------------------------------------------------------------
# get_current_execution
# ---------------------------------------------------------------------------


async def test_get_current_execution_no_executions_returns_none() -> None:
    em, _ = _make_em()
    result = await em.get_current_execution(uuid.uuid4())
    assert result is None


async def test_get_current_execution_after_start_returns_running() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)

    current = await em.get_current_execution(task_id)
    assert current is not None
    assert current.id == execution.id
    assert current.status == "running"


async def test_get_current_execution_after_complete_returns_none() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)
        await em.complete_execution(execution.id)

    current = await em.get_current_execution(task_id)
    assert current is None


async def test_get_current_execution_after_fail_returns_none() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)
        await em.fail_execution(execution.id, "something broke")

    current = await em.get_current_execution(task_id)
    assert current is None


# ---------------------------------------------------------------------------
# get_execution_history
# ---------------------------------------------------------------------------


async def test_get_execution_history_no_executions_returns_empty_list() -> None:
    em, _ = _make_em()
    history = await em.get_execution_history(uuid.uuid4())
    assert history == []


async def test_get_execution_history_after_two_executions_ordered_by_started_at() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        exec_a = await em.start_execution(task_id, spec_id)
        await em.complete_execution(exec_a.id)
        exec_b = await em.start_execution(task_id, spec_id)
        await em.fail_execution(exec_b.id, "timed out")

    history = await em.get_execution_history(task_id)
    assert len(history) == 2
    assert history[0].id == exec_a.id
    assert history[0].status == "completed"
    assert history[1].id == exec_b.id
    assert history[1].status == "failed"
    assert history[0].started_at <= history[1].started_at


async def test_get_execution_history_single_running_execution() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)

    history = await em.get_execution_history(task_id)
    assert len(history) == 1
    assert history[0].id == execution.id
    assert history[0].status == "running"


# ---------------------------------------------------------------------------
# Cleanup errors are logged but do not raise
# ---------------------------------------------------------------------------


async def test_cleanup_error_on_complete_does_not_raise() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP) as mock_cleanup:
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        mock_cleanup.side_effect = subprocess.CalledProcessError(128, "git")
        execution = await em.start_execution(task_id, spec_id)
        # Should not raise despite cleanup failure
        event = await em.complete_execution(execution.id)

    assert event.event_type == ev.EXECUTION_COMPLETED


async def test_cleanup_error_on_fail_does_not_raise() -> None:
    em, _ = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP) as mock_cleanup:
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        mock_cleanup.side_effect = subprocess.CalledProcessError(128, "git")
        execution = await em.start_execution(task_id, spec_id)
        event = await em.fail_execution(execution.id, "something failed")

    assert event.event_type == ev.EXECUTION_FAILED


# ---------------------------------------------------------------------------
# Worktree path convention
# ---------------------------------------------------------------------------


async def test_worktree_path_in_event_follows_convention() -> None:
    em, store = _make_em()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    with patch(PATCH_PREPARE) as mock_prepare, patch(PATCH_CLEANUP):
        mock_prepare.side_effect = lambda rp, eid: _fake_worktree_path(rp, eid)
        execution = await em.start_execution(task_id, spec_id)

    execution_events = await store.get_events(execution.id, "execution")
    worktree_path = execution_events[0].payload["worktree_path"]
    assert worktree_path == f"{REPO_PATH}/.worktrees/{execution.id}"
