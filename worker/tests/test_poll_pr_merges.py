"""Unit tests for poll_pr_merges in worker/runner.py."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from core import events as ev
from core.project_manager import ProjectManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from worker.runner import poll_pr_merges

FAKE_REPO_PATH = "/fake/repo"


async def _setup_project(store: InMemoryStore) -> tuple:
    """Register a project and return (project_manager, project)."""
    project_manager = ProjectManager(store)
    with patch("core.project_manager.validate_repo"):
        project = await project_manager.register_project(
            name="test-project",
            repo_url="https://github.com/test/repo",
            local_path=FAKE_REPO_PATH,
        )
    return project_manager, project


async def _setup_task_in_ready_for_deployment(
    store: InMemoryStore,
    project_id: uuid.UUID,
) -> uuid.UUID:
    """Create a task and advance it to ready_for_deployment."""
    task_id = uuid.uuid4()
    payload: dict = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "title": "PR test task",
        "status": ev.READY_FOR_SPEC,
        "refinement_count": 0,
        "required_capabilities": [],
    }
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload=payload,
    )
    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_id, ev.SPEC_QA)
    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    await state_machine.transition(task_id, ev.IN_PROGRESS)
    await state_machine.transition(task_id, ev.READY_FOR_QA)
    await state_machine.transition(task_id, ev.READY_FOR_DEPLOYMENT)
    return task_id


async def _emit_pr_created(store: InMemoryStore, task_id: uuid.UUID, pr_number: int) -> None:
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_PR_CREATED,
        payload={
            "pr_url": f"https://github.com/test/repo/pull/{pr_number}",
            "pr_number": pr_number,
            "branch": f"execution/{uuid.uuid4()}",
        },
    )


def _make_gh_proc(
    state: str,
    returncode: int = 0,
    merge_commit_sha: str | None = None,
) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    body: dict = {"state": state}
    if merge_commit_sha is not None:
        body["mergeCommit"] = {"oid": merge_commit_sha}
    proc.stdout = json.dumps(body)
    proc.stderr = ""
    return proc


@pytest.mark.asyncio
async def test_poll_pr_merges_merged_pr_transitions_to_deployed() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task_in_ready_for_deployment(store, project.id)
    await _emit_pr_created(store, task_id, pr_number=42)

    with patch("worker.runner._gh_command", return_value=_make_gh_proc("MERGED")):
        await poll_pr_merges(store, FAKE_REPO_PATH)

    task_manager = TaskManager(store)
    task = await task_manager.get_task(task_id)
    assert task is not None
    assert task.status == ev.DEPLOYED


@pytest.mark.asyncio
async def test_poll_pr_merges_open_pr_is_skipped() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task_in_ready_for_deployment(store, project.id)
    await _emit_pr_created(store, task_id, pr_number=7)

    with patch("worker.runner._gh_command", return_value=_make_gh_proc("OPEN")):
        await poll_pr_merges(store, FAKE_REPO_PATH)

    task_manager = TaskManager(store)
    task = await task_manager.get_task(task_id)
    assert task is not None
    assert task.status == ev.READY_FOR_DEPLOYMENT


@pytest.mark.asyncio
async def test_poll_pr_merges_skips_tasks_without_pr_created_event() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task_in_ready_for_deployment(store, project.id)
    # No TASK_PR_CREATED event emitted — local-mode task

    with patch("worker.runner._gh_command") as mock_gh:
        await poll_pr_merges(store, FAKE_REPO_PATH)
        mock_gh.assert_not_called()

    task_manager = TaskManager(store)
    task = await task_manager.get_task(task_id)
    assert task is not None
    assert task.status == ev.READY_FOR_DEPLOYMENT


@pytest.mark.asyncio
async def test_poll_pr_merges_captures_merge_commit_sha() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task_in_ready_for_deployment(store, project.id)
    await _emit_pr_created(store, task_id, pr_number=99)

    sha = "abc123def456abc123def456abc123def456abc1"
    mock_proc = _make_gh_proc("MERGED", merge_commit_sha=sha)
    with patch("worker.runner._gh_command", return_value=mock_proc):
        await poll_pr_merges(store, FAKE_REPO_PATH)

    task_manager = TaskManager(store)
    task = await task_manager.get_task(task_id)
    assert task is not None
    assert task.status == ev.DEPLOYED
    assert task.merge_commit_sha == sha

    # Verify the SHA is stored in the deployed event payload
    task_events = await store.get_events(task_id, "task")
    deployed_event = next(
        e for e in reversed(task_events)
        if e.event_type == ev.TASK_STATUS_CHANGED
        and e.payload.get("to_status") == ev.DEPLOYED
    )
    assert deployed_event.payload.get("merge_commit_sha") == sha


@pytest.mark.asyncio
async def test_poll_pr_merges_deployed_without_sha() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task_in_ready_for_deployment(store, project.id)
    await _emit_pr_created(store, task_id, pr_number=77)

    # No mergeCommit key in response
    mock_proc = _make_gh_proc("MERGED")
    with patch("worker.runner._gh_command", return_value=mock_proc):
        await poll_pr_merges(store, FAKE_REPO_PATH)

    task_manager = TaskManager(store)
    task = await task_manager.get_task(task_id)
    assert task is not None
    assert task.status == ev.DEPLOYED
    assert task.merge_commit_sha is None

    # Verify no merge_commit_sha key in deployed event payload
    task_events = await store.get_events(task_id, "task")
    deployed_event = next(
        e for e in reversed(task_events)
        if e.event_type == ev.TASK_STATUS_CHANGED
        and e.payload.get("to_status") == ev.DEPLOYED
    )
    assert "merge_commit_sha" not in deployed_event.payload
