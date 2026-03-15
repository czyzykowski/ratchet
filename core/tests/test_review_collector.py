"""Unit tests for ReviewDataCollector using InMemoryStore (no DB required)."""

import os
import subprocess as sp
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from core import events as ev
from core.models import ExecutionTrace, Project, ReviewScope
from core.review_collector import ReviewDataCollector
from core.store import InMemoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Project:
    """Create a Project with local_path=str(tmp_path) and a real git repo."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"}
    sp.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True, env=env)
    sp.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        env=env,
    )
    now = datetime.now(UTC)
    return Project(
        id=uuid4(),
        name="test-project",
        repo_url=str(tmp_path),
        local_path=str(tmp_path),
        status="active",
        created_at=now,
        updated_at=now,
    )


async def _seed_task(store: InMemoryStore, project_id: UUID) -> UUID:
    """Seed TASK_CREATED events, return task_id."""
    task_id = uuid4()
    payload = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "title": "Test task",
        "status": ev.READY_FOR_SPEC,
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
    return task_id


async def _seed_execution(
    store: InMemoryStore,
    task_id: UUID,
    spec_id: UUID,
    failure_reason: str | None = None,
) -> UUID:
    """Seed EXECUTION_STARTED and EXECUTION_COMPLETED/FAILED events, return execution_id."""
    execution_id = uuid4()
    started_payload = {
        "execution_id": str(execution_id),
        "task_id": str(task_id),
        "spec_id": str(spec_id),
        "worktree_path": "/tmp/worktree",
        "branch_name": f"execution/{execution_id}",
        "status": "running",
    }
    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload=started_payload,
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload=started_payload,
    )

    if failure_reason:
        await store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_FAILED,
            payload={
                "execution_id": str(execution_id),
                "failure_reason": failure_reason,
                "status": "failed",
            },
        )
    else:
        await store.append_event(
            aggregate_id=execution_id,
            aggregate_type="execution",
            event_type=ev.EXECUTION_COMPLETED,
            payload={
                "execution_id": str(execution_id),
                "status": "completed",
            },
        )
    return execution_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_collect_tasks_and_executions_for_project_scope(tmp_path: Path) -> None:
    store = InMemoryStore()
    project = _make_project(tmp_path)
    task_id1 = await _seed_task(store, project.id)
    task_id2 = await _seed_task(store, project.id)
    spec_id = uuid4()
    await _seed_execution(store, task_id1, spec_id)
    await _seed_execution(store, task_id2, spec_id)

    collector = ReviewDataCollector(store)
    scope = ReviewScope(project_ids=[project.id], include_global=False)
    result = await collector.collect(scope, [project])

    assert len(result.tasks) == 2
    assert len(result.executions_by_task[task_id1]) == 1
    assert len(result.executions_by_task[task_id2]) == 1


@pytest.mark.asyncio
async def test_should_skip_missing_trace_files_gracefully_without_raising(
    tmp_path: Path,
) -> None:
    store = InMemoryStore()
    project = _make_project(tmp_path)
    task_id = await _seed_task(store, project.id)
    spec_id = uuid4()
    execution_id = await _seed_execution(store, task_id, spec_id)

    # No trace saved to store — should not raise
    collector = ReviewDataCollector(store)
    scope = ReviewScope(project_ids=[project.id], include_global=False)

    result = await collector.collect(scope, [project])

    assert execution_id not in result.trace_contents


@pytest.mark.asyncio
async def test_should_include_global_claude_md_content_when_include_global_is_true(
    tmp_path: Path,
) -> None:
    store = InMemoryStore()
    fake_home = tmp_path / "home"
    claude_dir = fake_home / ".claude"
    claude_dir.mkdir(parents=True)
    expected_content = "# Global CLAUDE.md content"
    (claude_dir / "CLAUDE.md").write_text(expected_content)

    collector = ReviewDataCollector(store)
    scope = ReviewScope(project_ids=[], include_global=True)

    with patch("pathlib.Path.home", return_value=fake_home):
        result = await collector.collect(scope, [])

    assert result.global_claude_md == expected_content


@pytest.mark.asyncio
async def test_should_return_empty_string_for_global_claude_md_when_file_missing(
    tmp_path: Path,
) -> None:
    store = InMemoryStore()
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    collector = ReviewDataCollector(store)
    scope = ReviewScope(project_ids=[], include_global=True)

    with patch("pathlib.Path.home", return_value=fake_home):
        result = await collector.collect(scope, [])

    assert result.global_claude_md == ""


@pytest.mark.asyncio
async def test_should_collect_qa_failures_from_execution_failed_events(
    tmp_path: Path,
) -> None:
    store = InMemoryStore()
    project = _make_project(tmp_path)
    task_id = await _seed_task(store, project.id)
    spec_id = uuid4()
    await _seed_execution(store, task_id, spec_id, failure_reason="tests failed")

    collector = ReviewDataCollector(store)
    scope = ReviewScope(project_ids=[project.id], include_global=False)
    result = await collector.collect(scope, [project])

    assert len(result.qa_failures) == 1
    assert result.qa_failures[0]["failure_reason"] == "tests failed"


@pytest.mark.asyncio
async def test_should_truncate_trace_content_to_8000_chars(tmp_path: Path) -> None:
    store = InMemoryStore()
    project = _make_project(tmp_path)
    task_id = await _seed_task(store, project.id)
    spec_id = uuid4()
    execution_id = await _seed_execution(store, task_id, spec_id)

    now = datetime.now(UTC)
    store.save_trace(
        ExecutionTrace(
            execution_id=execution_id,
            task_id=task_id,
            spec_id=spec_id,
            content="x" * 9000,
            started_at=now,
            created_at=now,
        )
    )

    collector = ReviewDataCollector(store)
    scope = ReviewScope(project_ids=[project.id], include_global=False)

    result = await collector.collect(scope, [project])

    assert len(result.trace_contents[execution_id]) == 8000


@pytest.mark.asyncio
async def test_should_collect_git_log_via_subprocess_for_each_project(
    tmp_path: Path,
) -> None:
    store = InMemoryStore()
    project = _make_project(tmp_path)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "abc123 feat: init\n"

    collector = ReviewDataCollector(store)
    scope = ReviewScope(project_ids=[project.id], include_global=False)

    with patch("core.review_collector.subprocess.run", return_value=mock_result) as mock_run:
        result = await collector.collect(scope, [project])

    assert result.git_log[project.id] == "abc123 feat: init\n"
    mock_run.assert_called_once_with(
        ["git", "log", "--oneline", "-100"],
        cwd=project.local_path,
        capture_output=True,
        text=True,
    )
