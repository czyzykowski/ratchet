"""Integration tests for worker dispatch using InMemoryStore and mocked invoker.

Tests exercise ProjectDispatcher.impl_once() which replaced the old run_once() + get_next_task().
"""

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
from worker.dispatcher import ProjectDispatcher

PATCH_PREPARE = "core.execution_manager.prepare_task_environment"
PATCH_CLEANUP = "core.execution_manager.cleanup_task_environment"
PATCH_READ_INTENT = "core.context_assembler.read_intent"
PATCH_BASELINE_WORKTREE = "worker.dispatcher._create_baseline_worktree"
PATCH_REMOVE_QA_WORKTREE = "worker.dispatcher._remove_qa_worktree"

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
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload=task_payload,
    )
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
    """Transition a task from READY_FOR_SPEC -> SPEC_QA -> READY_FOR_IMPLEMENTATION."""
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
    eid = uuid.uuid4()
    invoker = MagicMock()
    invoker.invoke.return_value = InvocationResult(
        execution_id=eid,
        status=status,
        failure_reason=failure_reason,
        trace_id=eid,
    )
    return invoker


def _make_dispatcher(
    store: InMemoryStore,
    invoker: MagicMock | None = None,
    local_capabilities: list[str] | None = None,
) -> ProjectDispatcher:
    if invoker is None:
        invoker = _make_invoker("completed")
    return ProjectDispatcher(store, invoker, local_capabilities)


# ---------------------------------------------------------------------------
# No tasks ready
# ---------------------------------------------------------------------------


async def test_no_tasks_ready_logs_and_returns(caplog: pytest.LogCaptureFixture) -> None:
    store = InMemoryStore()
    dispatcher = _make_dispatcher(store)

    with caplog.at_level(logging.INFO, logger="worker.dispatcher"):
        result = await dispatcher.impl_once()

    assert result.action == "idle"
    assert "No tasks ready for implementation." in caplog.text


async def test_no_tasks_in_store_returns_idle() -> None:
    store = InMemoryStore()
    dispatcher = _make_dispatcher(store)
    result = await dispatcher.impl_once()
    assert result.action == "idle"


async def test_task_not_in_ready_state_is_skipped() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id, initial_status=ev.READY_FOR_SPEC)
    await _setup_spec(store, task_id)

    dispatcher = _make_dispatcher(store)
    result = await dispatcher.impl_once()

    assert result.action == "idle"


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

    dispatcher = _make_dispatcher(store)
    with caplog.at_level(logging.INFO, logger="worker.dispatcher"):
        result = await dispatcher.impl_once()

    assert result.action == "idle"
    assert "has no spec assigned, skipping" in caplog.text


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
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = _fake_worktree
        await dispatcher.impl_once()

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
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = _fake_worktree
        await dispatcher.impl_once()

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
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
        caplog.at_level(logging.INFO, logger="worker.dispatcher"),
    ):
        mock_prepare.side_effect = _fake_worktree
        await dispatcher.impl_once()

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
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = _fake_worktree
        await dispatcher.impl_once()

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
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = _fake_worktree
        await dispatcher.impl_once()

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
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = _fake_worktree
        await dispatcher.impl_once()

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
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = OSError("git worktree add failed")
        await dispatcher.impl_once()

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
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = OSError("git worktree add failed")
        await dispatcher.impl_once()

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
# waiting_for_input: impl_once filtering
# ---------------------------------------------------------------------------


async def test_waiting_for_input_task_skipped_when_pending_question_exists() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)

    # Unanswered question — task should be skipped.
    await _emit_qa_events(store, task_id, execution_id, answered=False)

    dispatcher = _make_dispatcher(store)
    result = await dispatcher.impl_once()
    assert result.action == "idle"


async def test_waiting_for_input_task_returned_when_question_answered() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)

    # Answered question — task should be picked up.
    await _emit_qa_events(store, task_id, execution_id, answered=True)

    mock_exec = _make_mock_execution(execution_id, task_id)
    invoker = _make_invoker("completed")
    dispatcher = _make_dispatcher(store, invoker)

    PATCH_GET_CURRENT_EXECUTION = "core.execution_manager.ExecutionManager.get_current_execution"
    PATCH_CONTEXT_ASSEMBLE = "core.context_assembler.ContextAssembler.assemble"

    from core.context_assembler import ExecutionContext
    fake_context = ExecutionContext(
        execution_id=execution_id,
        task_id=task_id,
        spec_id=uuid.uuid4(),
        worktree_path="/fake/repo/.worktrees/exec",
        prompt="# Spec\nDo the thing.",
    )

    with (
        patch(PATCH_GET_CURRENT_EXECUTION, new=AsyncMock(return_value=mock_exec)),
        patch(PATCH_CONTEXT_ASSEMBLE, new=AsyncMock(return_value=fake_context)),
        patch(PATCH_CLEANUP),
    ):
        result = await dispatcher.impl_once()

    assert result.action == "impl"
    assert result.task_id == task_id


# ---------------------------------------------------------------------------
# waiting_for_input: impl_once resume path
# ---------------------------------------------------------------------------

PATCH_GET_CURRENT_EXECUTION = "core.execution_manager.ExecutionManager.get_current_execution"
PATCH_CONTEXT_ASSEMBLE = "core.context_assembler.ContextAssembler.assemble"


async def test_resume_path_transitions_to_in_progress_then_ready_for_qa() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)
    await _emit_qa_events(store, task_id, execution_id, answered=True)

    mock_exec = _make_mock_execution(execution_id, task_id)

    from core.context_assembler import ExecutionContext
    fake_context = ExecutionContext(
        execution_id=execution_id,
        task_id=task_id,
        spec_id=uuid.uuid4(),
        worktree_path="/fake/repo/.worktrees/exec",
        prompt="# Spec\nDo the thing.",
    )
    invoker = _make_invoker("completed")
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_GET_CURRENT_EXECUTION, new=AsyncMock(return_value=mock_exec)),
        patch(PATCH_CONTEXT_ASSEMBLE, new=AsyncMock(return_value=fake_context)),
        patch(PATCH_CLEANUP),
    ):
        await dispatcher.impl_once()

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.READY_FOR_QA


async def test_resume_path_transitions_to_blocked_on_failed_invocation() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)
    await _emit_qa_events(store, task_id, execution_id, answered=True)

    mock_exec = _make_mock_execution(execution_id, task_id)

    from core.context_assembler import ExecutionContext
    fake_context = ExecutionContext(
        execution_id=execution_id,
        task_id=task_id,
        spec_id=uuid.uuid4(),
        worktree_path="/fake/repo/.worktrees/exec",
        prompt="# Spec\nDo the thing.",
    )
    invoker = _make_invoker("failed", failure_reason="tests failed")
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_GET_CURRENT_EXECUTION, new=AsyncMock(return_value=mock_exec)),
        patch(PATCH_CONTEXT_ASSEMBLE, new=AsyncMock(return_value=fake_context)),
        patch(PATCH_CLEANUP),
    ):
        await dispatcher.impl_once()

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.BLOCKED


async def test_resume_path_blocks_task_when_no_running_execution() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id, execution_id = await _setup_waiting_for_input_task(store, project.id)
    await _emit_qa_events(store, task_id, execution_id, answered=True)

    invoker = _make_invoker("completed")
    dispatcher = _make_dispatcher(store, invoker)

    with (
        patch(PATCH_GET_CURRENT_EXECUTION, new=AsyncMock(return_value=None)),
    ):
        result = await dispatcher.impl_once()

    assert result.action == "impl"
    assert result.success is False
    invoker.invoke.assert_not_called()

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.BLOCKED


@pytest.mark.asyncio
async def test_task_with_no_required_capabilities_always_matched() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(
        store, project.id, initial_status=ev.READY_FOR_SPEC, required_capabilities=[]
    )
    await _advance_task_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    invoker = _make_invoker("completed")
    dispatcher = _make_dispatcher(store, invoker, local_capabilities=[])

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = _fake_worktree
        result = await dispatcher.impl_once()

    assert result.action == "impl"


@pytest.mark.asyncio
async def test_task_with_matched_capabilities_is_eligible() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(
        store,
        project.id,
        initial_status=ev.READY_FOR_SPEC,
        required_capabilities=["docker"],
    )
    await _advance_task_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    invoker = _make_invoker("completed")
    dispatcher = _make_dispatcher(store, invoker, local_capabilities=["docker", "gpu"])

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
        patch(PATCH_BASELINE_WORKTREE, return_value="/fake/baseline"),
        patch(PATCH_REMOVE_QA_WORKTREE),
    ):
        mock_prepare.side_effect = _fake_worktree
        result = await dispatcher.impl_once()

    assert result.action == "impl"


@pytest.mark.asyncio
async def test_task_with_unmatched_capabilities_is_skipped() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(
        store,
        project.id,
        initial_status=ev.READY_FOR_SPEC,
        required_capabilities=["gpu"],
    )
    await _advance_task_to_ready(store, task_id)
    await _setup_spec(store, task_id)

    dispatcher = _make_dispatcher(store, local_capabilities=[])
    result = await dispatcher.impl_once()
    assert result.action == "idle"


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_orphaned_tasks_resets_in_progress_to_ready(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id, initial_status=ev.READY_FOR_SPEC)
    await _advance_task_to_ready(store, task_id)

    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    dispatcher = _make_dispatcher(store)
    with caplog.at_level(logging.WARNING):
        count = await dispatcher.recover_orphans()

    assert count == 1
    current_status = await state_machine.get_current_status(task_id)
    assert current_status == ev.READY_FOR_IMPLEMENTATION
    assert "Orphaned in_progress task at startup" in caplog.text


@pytest.mark.asyncio
async def test_recover_orphaned_tasks_ignores_non_in_progress() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id, initial_status=ev.READY_FOR_SPEC)
    await _advance_task_to_ready(store, task_id)

    state_machine = TaskStateMachine(store)
    dispatcher = _make_dispatcher(store)
    count = await dispatcher.recover_orphans()

    assert count == 0
    current_status = await state_machine.get_current_status(task_id)
    assert current_status == ev.READY_FOR_IMPLEMENTATION


@pytest.mark.asyncio
async def test_recover_orphaned_tasks_handles_multiple_projects() -> None:
    store = InMemoryStore()

    project_manager = ProjectManager(store)
    with patch("core.project_manager.validate_repo"):
        project_a = await project_manager.register_project(
            name="project-a", repo_url="https://github.com/a", local_path="/a"
        )
        project_b = await project_manager.register_project(
            name="project-b", repo_url="https://github.com/b", local_path="/b"
        )

    task_a = await _setup_task(store, project_a.id, initial_status=ev.READY_FOR_SPEC)
    task_b = await _setup_task(store, project_b.id, initial_status=ev.READY_FOR_SPEC)

    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_a, ev.SPEC_QA)
    await state_machine.transition(task_a, ev.READY_FOR_IMPLEMENTATION)
    await state_machine.transition(task_a, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    await state_machine.transition(task_b, ev.SPEC_QA)
    await state_machine.transition(task_b, ev.READY_FOR_IMPLEMENTATION)

    dispatcher = _make_dispatcher(store)
    count = await dispatcher.recover_orphans()

    assert count == 1
    assert await state_machine.get_current_status(task_a) == ev.READY_FOR_IMPLEMENTATION
    assert await state_machine.get_current_status(task_b) == ev.READY_FOR_IMPLEMENTATION
