"""Integration tests for worker runner using InMemoryStore and mocked invoker."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import events as ev
from core.invoker import InvocationResult
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from worker.runner import get_next_task, run_once

PATCH_PREPARE = "core.execution_manager.prepare_task_environment"
PATCH_CLEANUP = "core.execution_manager.cleanup_task_environment"
PATCH_READ_INTENT = "core.context_assembler.read_intent"

FAKE_REPO_PATH = "/fake/repo"
FAKE_WORKTREE_PATH = "/fake/repo/.worktrees/exec"


def _fake_worktree(repo_path: str, execution_id: uuid.UUID, claude_md: str | None = None) -> str:
    return f"{repo_path}/.worktrees/{execution_id}"


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


async def _setup_task(
    store: InMemoryStore,
    project_id: uuid.UUID,
    initial_status: str = ev.READY_FOR_SPEC,
    required_capabilities: list[str] = [],
) -> uuid.UUID:
    """Create a task in the store with project_tasks registry entry."""
    task_id = uuid.uuid4()
    task_payload: dict[str, object] = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "title": "Test task",
        "status": initial_status,
        "refinement_count": 0,
        "required_capabilities": required_capabilities,
    }
    # Store task events under the task aggregate.
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload=task_payload,
    )
    # Register task under project for discovery by get_next_task.
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload=task_payload,
    )
    return task_id


async def _advance_task_to_ready(
    store: InMemoryStore,
    task_id: uuid.UUID,
) -> None:
    """Transition a task from READY_FOR_SPEC → SPEC_QA → READY_FOR_IMPLEMENTATION."""
    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_id, ev.SPEC_QA)
    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)


async def _setup_spec(
    store: InMemoryStore,
    task_id: uuid.UUID,
) -> uuid.UUID:
    """Create and assign a spec for the task. Returns spec_id."""
    spec_manager = SpecManager(store)
    spec = await spec_manager.create_spec(task_id, "# Spec content\nDo the thing.")
    await spec_manager.assign_spec(task_id, spec.id)
    return spec.id


def _make_invoker(status: str, failure_reason: str | None = None) -> MagicMock:
    """Create a mocked ClaudeCodeInvoker that returns a given status."""
    invoker = MagicMock()
    invoker.invoke.return_value = InvocationResult(
        execution_id=uuid.uuid4(),
        status=status,
        failure_reason=failure_reason,
        trace_path="/tmp/trace.md",
    )
    return invoker


# ---------------------------------------------------------------------------
# No tasks ready
# ---------------------------------------------------------------------------


async def test_no_tasks_ready_logs_and_returns(caplog: pytest.LogCaptureFixture) -> None:
    store = InMemoryStore()
    invoker = _make_invoker("completed")

    with caplog.at_level(logging.INFO, logger="worker.runner"):
        await run_once(store, invoker)

    assert "No tasks ready for implementation." in caplog.text
    invoker.invoke.assert_not_called()


async def test_no_tasks_in_store_returns_none_from_get_next_task() -> None:
    store = InMemoryStore()
    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(store, project_manager, spec_manager, state_machine)
    assert result is None


async def test_task_not_in_ready_state_is_skipped() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id, initial_status=ev.READY_FOR_SPEC)
    await _setup_spec(store, task_id)

    invoker = _make_invoker("completed")

    # Task is in READY_FOR_SPEC, not READY_FOR_IMPLEMENTATION — should not be picked up.
    with patch(PATCH_PREPARE), patch(PATCH_CLEANUP), patch(PATCH_READ_INTENT):
        await run_once(store, invoker)

    invoker.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# Task with no spec is skipped
# ---------------------------------------------------------------------------


async def test_task_with_no_spec_is_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_task_to_ready(store, task_id)
    # No spec assigned.

    invoker = _make_invoker("completed")
    with caplog.at_level(logging.INFO, logger="worker.runner"):
        await run_once(store, invoker)

    assert "has no spec assigned, skipping" in caplog.text
    assert "No tasks ready for implementation." in caplog.text
    invoker.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------


async def test_successful_execution_transitions_task_to_ready_for_qa() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("completed")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
    ):
        mock_prepare.side_effect = _fake_worktree
        await run_once(store, invoker)

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.READY_FOR_QA


async def test_successful_execution_calls_complete_execution() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("completed")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
    ):
        mock_prepare.side_effect = _fake_worktree
        await run_once(store, invoker)

    # Verify EXECUTION_COMPLETED event was appended.
    all_events = store._events
    completed_events = [e for e in all_events if e.event_type == ev.EXECUTION_COMPLETED]
    assert len(completed_events) == 1


async def test_successful_execution_logs_completion(caplog: pytest.LogCaptureFixture) -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("completed")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        caplog.at_level(logging.INFO, logger="worker.runner"),
    ):
        mock_prepare.side_effect = _fake_worktree
        await run_once(store, invoker)

    assert "Execution completed" in caplog.text


# ---------------------------------------------------------------------------
# Failed invocation
# ---------------------------------------------------------------------------


async def test_failed_invocation_transitions_task_to_blocked() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("failed", failure_reason="tests failed")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
    ):
        mock_prepare.side_effect = _fake_worktree
        await run_once(store, invoker)

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.BLOCKED


async def test_failed_invocation_calls_fail_execution_with_reason() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("failed", failure_reason="tests failed")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
    ):
        mock_prepare.side_effect = _fake_worktree
        await run_once(store, invoker)

    all_events = store._events
    failed_events = [e for e in all_events if e.event_type == ev.EXECUTION_FAILED]
    assert len(failed_events) == 1
    assert failed_events[0].payload["failure_reason"] == "tests failed"


async def test_crashed_invocation_transitions_task_to_blocked() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("crashed", failure_reason="process exited with code 1")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
    ):
        mock_prepare.side_effect = _fake_worktree
        await run_once(store, invoker)

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.BLOCKED


# ---------------------------------------------------------------------------
# Environment preparation failure
# ---------------------------------------------------------------------------


async def test_env_prep_failure_transitions_task_to_blocked() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("completed")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
    ):
        mock_prepare.side_effect = OSError("git worktree add failed")
        await run_once(store, invoker)

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.BLOCKED


async def test_env_prep_failure_does_not_invoke_claude() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("completed")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
    ):
        mock_prepare.side_effect = OSError("git worktree add failed")
        await run_once(store, invoker)

    invoker.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers for waiting_for_input tests
# ---------------------------------------------------------------------------


async def _emit_qa_events(
    store: InMemoryStore,
    task_id: uuid.UUID,
    execution_id: uuid.UUID,
    answered: bool,
) -> None:
    """Emit TASK_INPUT_REQUESTED and optionally TASK_INPUT_PROVIDED events."""
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_INPUT_REQUESTED,
        payload={"question": "What color?", "execution_id": str(execution_id), "question_index": 0},
    )
    if answered:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_INPUT_PROVIDED,
            payload={"answer": "Blue", "question_index": 0, "answered_by": "cli"},
        )


async def _setup_waiting_for_input_task(
    store: InMemoryStore,
    project_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Set up a task in waiting_for_input state. Returns (task_id, execution_id)."""
    task_id = await _setup_task(store, project_id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)
    state_machine = TaskStateMachine(store)
    execution_id = uuid.uuid4()
    await state_machine.transition(task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})
    await state_machine.transition(
        task_id, ev.WAITING_FOR_INPUT, extra_payload={"execution_id": str(execution_id)}
    )
    return task_id, execution_id


def _make_mock_execution(execution_id: uuid.UUID, task_id: uuid.UUID) -> MagicMock:
    """Create a mock Execution with status='running'."""
    mock_exec = MagicMock()
    mock_exec.id = execution_id
    mock_exec.task_id = task_id
    mock_exec.spec_id = uuid.uuid4()
    mock_exec.branch_name = f"execution/{execution_id}"
    mock_exec.status = "running"
    return mock_exec


# ---------------------------------------------------------------------------
# waiting_for_input: get_next_task filtering
# ---------------------------------------------------------------------------


async def test_waiting_for_input_task_skipped_when_pending_question_exists() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)

    # Unanswered question — task should be skipped.
    await _emit_qa_events(store, task_id, execution_id, answered=False)

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(store, project_manager, spec_manager, state_machine)
    assert result is None


async def test_waiting_for_input_task_returned_when_question_answered() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)

    # Answered question — task should be returned.
    await _emit_qa_events(store, task_id, execution_id, answered=True)

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(store, project_manager, spec_manager, state_machine)
    assert result is not None
    assert result[0].id == task_id


# ---------------------------------------------------------------------------
# waiting_for_input: run_once resume path
# ---------------------------------------------------------------------------

PATCH_GET_CURRENT_EXECUTION = "core.execution_manager.ExecutionManager.get_current_execution"
PATCH_CONTEXT_ASSEMBLE = "core.context_assembler.ContextAssembler.assemble"


def _make_fake_context(execution_id: uuid.UUID, task_id: uuid.UUID) -> MagicMock:
    from core.context_assembler import ExecutionContext
    return ExecutionContext(
        execution_id=execution_id,
        task_id=task_id,
        spec_id=uuid.uuid4(),
        worktree_path="/fake/repo/.worktrees/exec",
        prompt="# Spec\nDo the thing.",
    )


async def test_resume_path_transitions_to_in_progress_then_ready_for_qa() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)
    await _emit_qa_events(store, task_id, execution_id, answered=True)

    mock_exec = _make_mock_execution(execution_id, task_id)
    fake_context = _make_fake_context(execution_id, task_id)
    invoker = _make_invoker("completed")

    with (
        patch(PATCH_GET_CURRENT_EXECUTION, new=AsyncMock(return_value=mock_exec)),
        patch(PATCH_CONTEXT_ASSEMBLE, new=AsyncMock(return_value=fake_context)),
        patch(PATCH_CLEANUP),
    ):
        await run_once(store, invoker)

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.READY_FOR_QA


async def test_resume_path_transitions_to_blocked_on_failed_invocation() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)
    await _emit_qa_events(store, task_id, execution_id, answered=True)

    mock_exec = _make_mock_execution(execution_id, task_id)
    fake_context = _make_fake_context(execution_id, task_id)
    invoker = _make_invoker("failed", failure_reason="tests failed")

    with (
        patch(PATCH_GET_CURRENT_EXECUTION, new=AsyncMock(return_value=mock_exec)),
        patch(PATCH_CONTEXT_ASSEMBLE, new=AsyncMock(return_value=fake_context)),
        patch(PATCH_CLEANUP),
    ):
        await run_once(store, invoker)

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.BLOCKED


async def test_resume_path_blocks_task_when_no_running_execution() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)
    await _emit_qa_events(store, task_id, execution_id, answered=True)

    invoker = _make_invoker("completed")

    with (
        patch(PATCH_GET_CURRENT_EXECUTION, new=AsyncMock(return_value=None)),
    ):
        result = await run_once(store, invoker)

    assert result is True
    invoker.invoke.assert_not_called()

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.BLOCKED


@pytest.mark.asyncio
async def test_task_with_no_required_capabilities_always_matched() -> None:
    store = InMemoryStore()
    project_manager, project = await _setup_project(store)
    task_id = await _setup_task(
        store, project.id, initial_status=ev.READY_FOR_SPEC, required_capabilities=[]
    )
    await _advance_task_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(
        store, project_manager, spec_manager, state_machine, local_capabilities=[]
    )
    assert result is not None


@pytest.mark.asyncio
async def test_task_with_matched_capabilities_is_eligible() -> None:
    store = InMemoryStore()
    project_manager, project = await _setup_project(store)
    task_id = await _setup_task(
        store,
        project.id,
        initial_status=ev.READY_FOR_SPEC,
        required_capabilities=["docker"],
    )
    await _advance_task_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(
        store,
        project_manager,
        spec_manager,
        state_machine,
        local_capabilities=["docker", "gpu"],
    )
    assert result is not None


@pytest.mark.asyncio
async def test_task_with_unmatched_capabilities_is_skipped() -> None:
    store = InMemoryStore()
    project_manager, project = await _setup_project(store)
    task_id = await _setup_task(
        store,
        project.id,
        initial_status=ev.READY_FOR_SPEC,
        required_capabilities=["gpu"],
    )
    await _advance_task_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(
        store, project_manager, spec_manager, state_machine, local_capabilities=[]
    )
    assert result is None
