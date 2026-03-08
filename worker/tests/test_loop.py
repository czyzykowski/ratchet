"""Tests for continuous loop behaviour: return values and main_loop control flow."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from core import events as ev
from core.invoker import InvocationResult
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from worker.runner import main_loop, run_once, run_qa_once

PATCH_VALIDATE_REPO = "core.project_manager.validate_repo"
PATCH_PREPARE = "core.execution_manager.prepare_task_environment"
PATCH_CLEANUP = "core.execution_manager.cleanup_task_environment"
PATCH_READ_INTENT = "core.context_assembler.read_intent"

FAKE_REPO_PATH = "/fake/repo"


def _fake_worktree(repo_path: str, execution_id: uuid.UUID) -> str:
    return f"{repo_path}/.worktrees/{execution_id}"


async def _setup_project(store: InMemoryStore):
    project_manager = ProjectManager(store)
    with patch(PATCH_VALIDATE_REPO):
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
) -> uuid.UUID:
    task_id = uuid.uuid4()
    task_payload = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "title": "Test task",
        "status": initial_status,
        "refinement_count": 0,
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


async def _setup_spec(store: InMemoryStore, task_id: uuid.UUID) -> uuid.UUID:
    spec_manager = SpecManager(store)
    spec = await spec_manager.create_spec(task_id, "# Spec content\nDo the thing.")
    await spec_manager.assign_spec(task_id, spec.id)
    return spec.id


async def _advance_to_ready_for_impl(store: InMemoryStore, task_id: uuid.UUID) -> None:
    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_id, ev.SPEC_QA)
    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)


async def _advance_to_ready_for_qa(store: InMemoryStore, task_id: uuid.UUID) -> None:
    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_id, ev.SPEC_QA)
    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    await state_machine.transition(task_id, ev.IN_PROGRESS)
    await state_machine.transition(task_id, ev.READY_FOR_QA)


def _make_invoker(status: str = "completed") -> MagicMock:
    invoker = MagicMock()
    invoker.invoke.return_value = InvocationResult(
        execution_id=uuid.uuid4(),
        status=status,
        failure_reason=None,
        trace_path="/tmp/trace.md",
    )
    return invoker


# ---------------------------------------------------------------------------
# run_once return value
# ---------------------------------------------------------------------------


async def test_run_once_returns_false_when_no_tasks() -> None:
    store = InMemoryStore()
    invoker = _make_invoker()

    result = await run_once(store, invoker)

    assert result is False


async def test_run_once_returns_true_when_task_found() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_to_ready_for_impl(store, task_id)

    invoker = _make_invoker("completed")

    with (
        patch(PATCH_PREPARE) as mock_prepare,
        patch(PATCH_CLEANUP),
        patch(PATCH_READ_INTENT, return_value="# Intent"),
    ):
        mock_prepare.side_effect = _fake_worktree
        result = await run_once(store, invoker)

    assert result is True


# ---------------------------------------------------------------------------
# run_qa_once return value
# ---------------------------------------------------------------------------


async def test_run_qa_once_returns_false_when_no_tasks() -> None:
    store = InMemoryStore()

    result = await run_qa_once(store)

    assert result is False


async def test_run_qa_once_returns_true_when_qa_task_found() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_to_ready_for_qa(store, task_id)

    with patch("worker.runner.load_qa_config", return_value=None):
        result = await run_qa_once(store)

    assert result is True


# ---------------------------------------------------------------------------
# main_loop control flow
# ---------------------------------------------------------------------------


async def test_main_loop_exits_on_keyboard_interrupt() -> None:
    store = InMemoryStore()
    invoker = _make_invoker()

    call_count = 0

    async def fake_run_once(*args, **kwargs) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt
        return False

    with (
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", return_value=False),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        # Should not raise
        await main_loop(store, invoker)


async def test_main_loop_skips_sleep_when_impl_task_processed() -> None:
    store = InMemoryStore()
    invoker = _make_invoker()

    call_count = 0
    sleep_mock = AsyncMock()

    async def fake_run_once(*args, **kwargs) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt
        return True  # task was processed

    with (
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", return_value=False),
        patch("asyncio.sleep", sleep_mock),
    ):
        await main_loop(store, invoker)

    sleep_mock.assert_not_called()


async def test_main_loop_sleeps_when_no_tasks_found() -> None:
    store = InMemoryStore()
    invoker = _make_invoker()

    call_count = 0
    sleep_mock = AsyncMock()

    async def fake_run_once(*args, **kwargs) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt
        return False

    with (
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", return_value=False),
        patch("asyncio.sleep", sleep_mock),
    ):
        await main_loop(store, invoker)

    sleep_mock.assert_called_once_with(30)
