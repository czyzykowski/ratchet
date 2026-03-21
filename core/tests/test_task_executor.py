"""Tests for TaskExecutor."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from core.context_assembler import ExecutionContext
from core.invoker import InvocationResult
from core.models import Execution, Project, Spec, Task
from core.task_executor import ExecutionOutcome, TaskExecutor


def _make_task(**overrides) -> Task:
    defaults = dict(
        id=uuid4(),
        project_id=uuid4(),
        title="test task",
        status="ready_for_implementation",
        current_spec_id=uuid4(),
        refinement_count=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        depends_on=[],
        required_capabilities=[],
        merge_commit_sha=None,
    )
    defaults.update(overrides)
    return Task(**defaults)


def _make_spec(**overrides) -> Spec:
    defaults = dict(
        id=uuid4(),
        task_id=uuid4(),
        content="do the thing",
        previous_spec_id=None,
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Spec(**defaults)


def _make_project(**overrides) -> Project:
    defaults = dict(
        id=uuid4(),
        name="test-project",
        repo_url="/tmp/repo",
        local_path="/tmp/repo",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        config_source="disk",
        claude_md=None,
        ratchet_yaml=None,
        intent_md=None,
    )
    defaults.update(overrides)
    return Project(**defaults)


def _make_execution(**overrides) -> Execution:
    defaults = dict(
        id=uuid4(),
        task_id=uuid4(),
        spec_id=uuid4(),
        status="running",
        failure_reason=None,
        branch_name="execution/test",
        started_at=datetime.now(UTC),
        completed_at=None,
    )
    defaults.update(overrides)
    return Execution(**defaults)


def _make_context(execution_id=None, **overrides) -> ExecutionContext:
    defaults = dict(
        execution_id=execution_id or uuid4(),
        task_id=uuid4(),
        spec_id=uuid4(),
        worktree_path="/tmp/repo/.worktrees/test-wt",
        prompt="do the thing",
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)


def _make_invocation_result(
    status: str = "completed",
    execution_id=None,
    failure_reason: str | None = None,
) -> InvocationResult:
    eid = execution_id or uuid4()
    return InvocationResult(
        execution_id=eid,
        status=status,
        failure_reason=failure_reason,
        trace_id=eid,
    )


@pytest.mark.asyncio
async def test_execute_happy_path_returns_completed():
    """execute() should return COMPLETED when invoker succeeds,
    and mark the execution as complete."""
    task = _make_task()
    spec = _make_spec(task_id=task.id)
    project = _make_project()
    execution = _make_execution(task_id=task.id, spec_id=spec.id)
    context = _make_context(execution_id=execution.id)
    inv_result = _make_invocation_result("completed", execution_id=execution.id)

    em = AsyncMock()
    em.start_execution = AsyncMock(return_value=execution)
    em.complete_execution = AsyncMock()

    ca = AsyncMock()
    ca.assemble = AsyncMock(return_value=context)

    invoker = MagicMock()
    invoker.invoke = MagicMock(return_value=inv_result)

    executor = TaskExecutor(em, ca, invoker)
    result = await executor.execute(task, spec, project)

    assert result.outcome == ExecutionOutcome.COMPLETED
    assert result.execution_id == execution.id
    assert result.failure_reason is None
    assert result.trace_id == inv_result.trace_id
    em.start_execution.assert_awaited_once_with(task.id, spec.id, project)
    ca.assemble.assert_awaited_once_with(execution.id, project)


@pytest.mark.asyncio
async def test_execute_invoker_failure_returns_blocked():
    """execute() should return BLOCKED when invoker returns failed."""
    task = _make_task()
    spec = _make_spec(task_id=task.id)
    project = _make_project()
    execution = _make_execution(task_id=task.id)
    context = _make_context(execution_id=execution.id)

    em = AsyncMock()
    em.start_execution = AsyncMock(return_value=execution)
    em.fail_execution = AsyncMock()

    ca = AsyncMock()
    ca.assemble = AsyncMock(return_value=context)

    invoker = MagicMock()
    invoker.invoke = MagicMock(
        return_value=_make_invocation_result("failed", failure_reason="no completion marker")
    )

    executor = TaskExecutor(em, ca, invoker)
    result = await executor.execute(task, spec, project)

    assert result.outcome == ExecutionOutcome.BLOCKED
    assert result.failure_reason == "no completion marker"
    em.fail_execution.assert_awaited_once_with(execution.id, "no completion marker")


@pytest.mark.asyncio
async def test_execute_env_failure_returns_env_error():
    """execute() should return ENV_ERROR when start_execution raises OSError."""
    task = _make_task()
    spec = _make_spec(task_id=task.id)
    project = _make_project()

    em = AsyncMock()
    em.start_execution = AsyncMock(side_effect=OSError("worktree failed"))

    executor = TaskExecutor(em, AsyncMock(), MagicMock())
    result = await executor.execute(task, spec, project)

    assert result.outcome == ExecutionOutcome.ENV_ERROR
    assert "worktree failed" in result.failure_reason
    assert result.trace_id is None


@pytest.mark.asyncio
async def test_execute_context_failure_returns_env_error():
    """execute() should return ENV_ERROR when assemble raises ContextAssemblyError."""
    from core.context_assembler import ContextAssemblyError

    task = _make_task()
    spec = _make_spec(task_id=task.id)
    project = _make_project()
    execution = _make_execution(task_id=task.id)

    em = AsyncMock()
    em.start_execution = AsyncMock(return_value=execution)
    em.fail_execution = AsyncMock()

    ca = AsyncMock()
    ca.assemble = AsyncMock(side_effect=ContextAssemblyError("INTENT.md not found"))

    executor = TaskExecutor(em, ca, MagicMock())
    result = await executor.execute(task, spec, project)

    assert result.outcome == ExecutionOutcome.ENV_ERROR
    assert "INTENT.md not found" in result.failure_reason
    em.fail_execution.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_invoker_crash_propagates_after_cleanup():
    """execute() should mark execution failed and re-raise on unexpected crash."""
    task = _make_task()
    spec = _make_spec(task_id=task.id)
    project = _make_project()
    execution = _make_execution(task_id=task.id)
    context = _make_context(execution_id=execution.id)

    em = AsyncMock()
    em.start_execution = AsyncMock(return_value=execution)
    em.fail_execution = AsyncMock()

    ca = AsyncMock()
    ca.assemble = AsyncMock(return_value=context)

    invoker = MagicMock()
    invoker.invoke = MagicMock(side_effect=RuntimeError("segfault"))

    executor = TaskExecutor(em, ca, invoker)
    with pytest.raises(RuntimeError, match="segfault"):
        await executor.execute(task, spec, project)

    em.fail_execution.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_happy_path_returns_completed():
    """resume() should complete without creating a worktree."""
    task = _make_task()
    project = _make_project()
    execution_id = uuid4()
    context = _make_context(execution_id=execution_id)

    em = AsyncMock()
    em.complete_execution = AsyncMock()

    ca = AsyncMock()
    ca.assemble = AsyncMock(return_value=context)

    inv_result = _make_invocation_result("completed", execution_id=execution_id)
    invoker = MagicMock()
    invoker.invoke = MagicMock(return_value=inv_result)

    executor = TaskExecutor(em, ca, invoker)
    result = await executor.resume(task, project, execution_id)

    assert result.outcome == ExecutionOutcome.COMPLETED
    assert result.execution_id == execution_id
    em.start_execution.assert_not_awaited()
    em.complete_execution.assert_awaited_once_with(execution_id)


@pytest.mark.asyncio
async def test_resume_failure_returns_blocked():
    """resume() should return BLOCKED when invoker fails."""
    task = _make_task()
    project = _make_project()
    execution_id = uuid4()
    context = _make_context(execution_id=execution_id)

    em = AsyncMock()
    em.fail_execution = AsyncMock()

    ca = AsyncMock()
    ca.assemble = AsyncMock(return_value=context)

    invoker = MagicMock()
    invoker.invoke = MagicMock(
        return_value=_make_invocation_result("failed", failure_reason="blocked on question")
    )

    executor = TaskExecutor(em, ca, invoker)
    result = await executor.resume(task, project, execution_id)

    assert result.outcome == ExecutionOutcome.BLOCKED
    assert result.failure_reason == "blocked on question"
    em.start_execution.assert_not_awaited()
    em.fail_execution.assert_awaited_once()
