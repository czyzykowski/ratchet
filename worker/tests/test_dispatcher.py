"""Tests for ProjectDispatcher."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from core import events as ev
from core.invoker import InvocationResult
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from worker.dispatcher import ProjectDispatcher

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


async def _setup_task(
    store: InMemoryStore,
    project_id: uuid.UUID,
    initial_status: str = ev.READY_FOR_SPEC,
    required_capabilities: list[str] | None = None,
) -> uuid.UUID:
    """Create a task in the store with project_tasks registry entry."""
    task_id = uuid.uuid4()
    task_payload: dict[str, object] = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "title": "Test task",
        "status": initial_status,
        "refinement_count": 0,
        "required_capabilities": required_capabilities or [],
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


def _make_invoker(status: str = "completed", failure_reason: str | None = None) -> MagicMock:
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
        invoker = _make_invoker()
    d = ProjectDispatcher(store, invoker, local_capabilities)
    d.orphan_grace_seconds = 0  # disable grace period in tests
    return d


# ---------------------------------------------------------------------------
# recover_orphans
# ---------------------------------------------------------------------------


async def test_recover_orphans_resets_in_progress_task() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_task_to_ready(store, task_id)

    # Transition to in_progress (simulating a crashed worker)
    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    dispatcher = _make_dispatcher(store)
    count = await dispatcher.recover_orphans()

    assert count == 1
    task = await TaskManager(store).get_task(task_id)
    assert task is not None
    assert task.status == ev.READY_FOR_IMPLEMENTATION


# ---------------------------------------------------------------------------
# impl_once
# ---------------------------------------------------------------------------

PATCH_PREPARE = "core.execution_manager.prepare_task_environment"
PATCH_CLEANUP = "core.execution_manager.cleanup_task_environment"
PATCH_READ_INTENT = "core.context_assembler.read_intent"
PATCH_BASELINE_WORKTREE = "worker.pipelines.impl.create_baseline_worktree"
PATCH_REMOVE_QA_WORKTREE = "worker.pipelines.impl.remove_qa_worktree"


def _fake_worktree(repo_path: str, execution_id: uuid.UUID, claude_md: str | None = None) -> str:
    return f"{repo_path}/.worktrees/{execution_id}"


async def test_impl_once_returns_idle_when_no_tasks_ready() -> None:
    store = InMemoryStore()
    dispatcher = _make_dispatcher(store)
    result = await dispatcher.impl_once()
    assert result.action == "idle"
    assert result.task_id is None


async def test_impl_once_skips_task_without_spec() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_task_to_ready(store, task_id)
    # No spec assigned

    dispatcher = _make_dispatcher(store)
    result = await dispatcher.impl_once()

    assert result.action == "idle"


async def test_impl_once_skips_task_with_unmatched_capabilities() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(
        store, project.id, required_capabilities=["gpu"]
    )
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    dispatcher = _make_dispatcher(store, local_capabilities=[])
    result = await dispatcher.impl_once()

    assert result.action == "idle"


async def test_impl_once_picks_task_with_matched_capabilities() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(
        store, project.id, required_capabilities=["gpu"]
    )
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("completed")
    dispatcher = _make_dispatcher(store, invoker, local_capabilities=["gpu", "fast"])

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
    assert result.task_id == task_id


async def test_impl_once_skips_project_with_in_progress_task() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)

    # Task 1: in_progress (blocks the project)
    t1 = await _setup_task(store, project.id)
    await _advance_task_to_ready(store, t1)
    sm = TaskStateMachine(store)
    await sm.transition(t1, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    # Task 2: ready for implementation
    t2 = await _setup_task(store, project.id)
    await _setup_spec(store, t2)
    await _advance_task_to_ready(store, t2)

    dispatcher = _make_dispatcher(store)
    result = await dispatcher.impl_once()

    assert result.action == "idle"


async def test_impl_once_transitions_to_blocked_on_failed_invocation() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    invoker = _make_invoker("failed", failure_reason="syntax error in output")
    dispatcher = _make_dispatcher(store, invoker)

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
    assert result.task_id == task_id
    task = await TaskManager(store).get_task(task_id)
    assert task is not None
    assert task.status == ev.BLOCKED


async def test_impl_once_executes_ready_task_and_transitions_to_qa() -> None:
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
        result = await dispatcher.impl_once()

    assert result.action == "impl"
    assert result.task_id == task_id
    task = await TaskManager(store).get_task(task_id)
    assert task is not None
    assert task.status == ev.READY_FOR_QA


async def test_recover_orphans_across_multiple_projects() -> None:
    store = InMemoryStore()
    _, project1 = await _setup_project(store)
    _, project2 = await _setup_project(store)
    t1 = await _setup_task(store, project1.id)
    t2 = await _setup_task(store, project2.id)
    await _advance_task_to_ready(store, t1)
    await _advance_task_to_ready(store, t2)
    sm = TaskStateMachine(store)
    await sm.transition(t1, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})
    await sm.transition(t2, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    dispatcher = _make_dispatcher(store)
    count = await dispatcher.recover_orphans()

    assert count == 2


async def test_recover_orphans_ignores_non_in_progress_tasks() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _advance_task_to_ready(store, task_id)
    # Task is in READY_FOR_IMPLEMENTATION, not IN_PROGRESS

    dispatcher = _make_dispatcher(store)
    count = await dispatcher.recover_orphans()

    assert count == 0
    task = await TaskManager(store).get_task(task_id)
    assert task is not None
    assert task.status == ev.READY_FOR_IMPLEMENTATION


# ---------------------------------------------------------------------------
# qa_once
# ---------------------------------------------------------------------------

PATCH_QA_WORKTREE = "worker.pipelines.qa.create_qa_worktree"
PATCH_LOAD_QA_CONFIG = "worker.pipelines.qa.load_qa_config"
PATCH_RUN_QA_STEPS = "worker.pipelines.qa.run_qa_steps"
PATCH_RUN_AUTO_FIXES = "worker.pipelines.qa.run_auto_fixes"
PATCH_GET_GIT_DIFF = "worker.pipelines.qa.get_git_diff"
PATCH_BUILD_REVIEW = "worker.pipelines.qa.build_review_prompt"
PATCH_PARSE_REVIEW = "worker.pipelines.qa.parse_review_output"
PATCH_SUBPROCESS_RUN = "core.claude_subprocess.subprocess.Popen"


async def _setup_task_for_qa(
    store: InMemoryStore, project_id: uuid.UUID
) -> uuid.UUID:
    """Create a task and advance it to ready_for_qa with an execution branch."""
    task_id = await _setup_task(store, project_id)
    spec_id = await _setup_spec(store, task_id)
    await _advance_task_to_ready(store, task_id)

    sm = TaskStateMachine(store)
    await sm.transition(task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    # Record an EXECUTION_STARTED event with branch name
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload={
            "branch_name": f"execution/{task_id}",
            "spec_id": str(spec_id),
        },
    )

    await sm.transition(task_id, ev.READY_FOR_QA)
    return task_id


async def test_qa_once_returns_idle_when_no_qa_tasks() -> None:
    store = InMemoryStore()
    dispatcher = _make_dispatcher(store)
    result = await dispatcher.qa_once()
    assert result.action == "idle"


async def test_qa_once_transitions_to_deployed_when_no_qa_config() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task_for_qa(store, project.id)

    dispatcher = _make_dispatcher(store)

    with patch(PATCH_LOAD_QA_CONFIG, return_value=None):
        result = await dispatcher.qa_once()

    assert result.action == "qa"
    assert result.task_id == task_id
    task = await TaskManager(store).get_task(task_id)
    assert task is not None
    assert task.status == ev.READY_FOR_DEPLOYMENT


# ---------------------------------------------------------------------------
# merge_once
# ---------------------------------------------------------------------------

PATCH_LOAD_DEPLOY_CONFIG = "worker.pipelines.merge.load_deployment_config"
PATCH_SQUASH_MERGE = "worker.pipelines.merge.squash_merge"
PATCH_LOAD_MERGE_CONFIG = "worker.pipelines.merge.load_merge_config"
PATCH_READ_INTENT_DISPATCH = "worker.pipelines.merge.read_intent"


async def test_merge_once_returns_idle_when_no_tasks() -> None:
    store = InMemoryStore()
    dispatcher = _make_dispatcher(store)
    result = await dispatcher.merge_once()
    assert result.action == "idle"


# ---------------------------------------------------------------------------
# dispatch (priority chain)
# ---------------------------------------------------------------------------


async def test_dispatch_tries_merge_then_qa_then_impl() -> None:
    """When all return idle, dispatch returns idle."""
    store = InMemoryStore()
    _, project = await _setup_project(store)
    dispatcher = _make_dispatcher(store)
    result = await dispatcher.dispatch(project.id)
    assert result.action == "idle"


async def test_dispatch_returns_qa_result_when_merge_idle() -> None:
    """QA task should be picked when no merge candidates exist."""
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task_for_qa(store, project.id)

    dispatcher = _make_dispatcher(store)

    with patch(PATCH_LOAD_QA_CONFIG, return_value=None):
        result = await dispatcher.dispatch(project.id)

    assert result.action == "qa"
    assert result.task_id == task_id
