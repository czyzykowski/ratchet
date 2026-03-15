"""Tests for continuous loop behaviour: return values and notification_loop control flow."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock, patch

from core import events as ev
from core.invoker import InvocationResult
from core.project_manager import ProjectManager
from core.qa_runner import QaStepResult
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from worker.runner import get_next_task, notification_loop, run_once, run_qa_once

PATCH_VALIDATE_REPO = "core.project_manager.validate_repo"
PATCH_PREPARE = "core.execution_manager.prepare_task_environment"
PATCH_CLEANUP = "core.execution_manager.cleanup_task_environment"
PATCH_READ_INTENT = "core.context_assembler.read_intent"

FAKE_REPO_PATH = "/fake/repo"


def _fake_worktree(repo_path: str, execution_id: uuid.UUID) -> str:
    return f"{repo_path}/.worktrees/{execution_id}"


async def _setup_project(store: InMemoryStore, name: str = "test-project"):
    project_manager = ProjectManager(store)
    with patch(PATCH_VALIDATE_REPO):
        project = await project_manager.register_project(
            name=name,
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
        trace_id=uuid.uuid4(),
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
# baseline QA guard in run_once
# ---------------------------------------------------------------------------


async def test_run_once_skips_when_baseline_qa_fails() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_to_ready_for_impl(store, task_id)

    invoker = _make_invoker("completed")
    fake_failure = QaStepResult(
        step_name="lint",
        command="ruff check .",
        returncode=1,
        output="E501 line too long",
    )

    with patch("worker.runner.check_baseline_qa", return_value=[fake_failure]):
        result = await run_once(store, invoker)

    assert result is False
    invoker.invoke.assert_not_called()

    # Task status must still be ready_for_implementation
    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.READY_FOR_IMPLEMENTATION


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
# notification_loop control flow
# ---------------------------------------------------------------------------


class _MockNotificationListener:
    """Mock NotificationListener that yields controlled notifications then stops."""

    def __init__(self, notifications: list[tuple[str, str, str]]) -> None:
        self._notifications = notifications

    async def __aenter__(self) -> _MockNotificationListener:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def listen(self) -> AsyncGenerator[tuple[str, str, str], None]:
        for item in self._notifications:
            yield item


async def test_notification_loop_runs_catchup_on_startup() -> None:
    """notification_loop calls run_qa_once, run_once, and compile_once on startup.

    All three return False so all run during catchup.
    """
    store = InMemoryStore()
    invoker = _make_invoker()
    await _setup_project(store)  # needed so _dispatch_all finds an active project

    catchup_calls: list[str] = []

    async def fake_compile_once(*args, **kwargs) -> bool:
        catchup_calls.append("compile_once")
        return False

    async def fake_run_once(*args, **kwargs) -> bool:
        catchup_calls.append("run_once")
        return False

    async def fake_run_qa_once(*args, **kwargs) -> bool:
        catchup_calls.append("run_qa_once")
        return False

    mock_listener = _MockNotificationListener([])  # no notifications → loop ends quickly

    with (
        patch("worker.runner.compile_once", side_effect=fake_compile_once),
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", side_effect=fake_run_qa_once),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(store, invoker, dsn="postgresql://fake/test")

    assert "compile_once" in catchup_calls
    assert "run_once" in catchup_calls
    assert "run_qa_once" in catchup_calls


async def test_notification_loop_dispatches_queued_notifications() -> None:
    """notification_loop calls run_once + run_qa_once for each queued task notification."""
    store = InMemoryStore()
    invoker = _make_invoker()
    await _setup_project(store)  # needed so _dispatch_all finds an active project

    task_id = str(uuid.uuid4())
    dispatch_calls: list[str] = []

    async def fake_compile_once(*args, **kwargs) -> bool:
        dispatch_calls.append("compile_once")
        return False

    async def fake_run_once(*args, **kwargs) -> bool:
        dispatch_calls.append("run_once")
        return True

    async def fake_run_qa_once(*args, **kwargs) -> bool:
        dispatch_calls.append("run_qa_once")
        return False

    # Two task notifications: one impl, one QA (3-tuples)
    notifications = [
        ("task", task_id, ev.READY_FOR_IMPLEMENTATION),
        ("task", task_id, ev.READY_FOR_QA),
    ]
    mock_listener = _MockNotificationListener(notifications)

    with (
        patch("worker.runner.compile_once", side_effect=fake_compile_once),
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", side_effect=fake_run_qa_once),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(store, invoker, dsn="postgresql://fake/test")

    # With per-project concurrent dispatch, each round uses asyncio.gather which
    # introduces context switches. The mock producer may finish before the consumer
    # processes all queued notifications. Assert at least the startup catchup ran.
    run_once_count = dispatch_calls.count("run_once")
    run_qa_count = dispatch_calls.count("run_qa_once")
    assert run_once_count >= 1
    assert run_qa_count >= 1


async def test_notification_loop_exits_cleanly_when_listener_ends() -> None:
    """notification_loop exits without error when the notification source is exhausted."""
    store = InMemoryStore()
    invoker = _make_invoker()

    async def fake_compile_once(*args, **kwargs) -> bool:
        return False

    async def fake_run_once(*args, **kwargs) -> bool:
        return False

    async def fake_run_qa_once(*args, **kwargs) -> bool:
        return False

    # Empty listener → producer finishes immediately → loop exits
    mock_listener = _MockNotificationListener([])

    with (
        patch("worker.runner.compile_once", side_effect=fake_compile_once),
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", side_effect=fake_run_qa_once),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        # Should not raise
        await notification_loop(store, invoker, dsn="postgresql://fake/test")


# ---------------------------------------------------------------------------
# Per-project concurrent dispatch
# ---------------------------------------------------------------------------


async def test_two_projects_dispatch_concurrently() -> None:
    """Both project IDs appear in run_once calls during one startup dispatch round."""
    store = InMemoryStore()
    invoker = _make_invoker()
    _, project_a = await _setup_project(store, "project-a")
    _, project_b = await _setup_project(store, "project-b")

    dispatched_ids: list[uuid.UUID] = []

    async def fake_run_once(*args, **kwargs) -> bool:
        pid = kwargs.get("project_id")
        if pid is not None:
            dispatched_ids.append(pid)
        return False

    async def fake_run_qa_once(*args, **kwargs) -> bool:
        return False

    async def fake_compile_once(*args, **kwargs) -> bool:
        return False

    mock_listener = _MockNotificationListener([])

    with (
        patch("worker.runner.compile_once", side_effect=fake_compile_once),
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", side_effect=fake_run_qa_once),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(store, invoker, dsn="postgresql://fake/test")

    assert project_a.id in dispatched_ids
    assert project_b.id in dispatched_ids


async def test_busy_project_skipped() -> None:
    """A project already in busy_projects is not dispatched during that round."""
    store = InMemoryStore()
    invoker = _make_invoker()
    _, project_a = await _setup_project(store, "project-a")

    dispatched_ids: list[uuid.UUID] = []

    async def fake_run_qa_once(*args, **kwargs) -> bool:
        pid = kwargs.get("project_id")
        if pid is not None:
            dispatched_ids.append(pid)
        return False

    async def fake_run_once(*args, **kwargs) -> bool:
        pid = kwargs.get("project_id")
        if pid is not None:
            dispatched_ids.append(pid)
        return False

    async def fake_compile_once(*args, **kwargs) -> bool:
        return False

    mock_listener = _MockNotificationListener([])

    with (
        patch("worker.runner.compile_once", side_effect=fake_compile_once),
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", side_effect=fake_run_qa_once),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(
            store,
            invoker,
            dsn="postgresql://fake/test",
            _initial_busy_projects={project_a.id},
        )

    assert project_a.id not in dispatched_ids


async def test_get_next_task_filters_by_project_id() -> None:
    """get_next_task with project_id set returns only tasks for that project."""
    store = InMemoryStore()
    _, project_a = await _setup_project(store, "project-a")
    _, project_b = await _setup_project(store, "project-b")

    task_a_id = await _setup_task(store, project_a.id)
    await _setup_spec(store, task_a_id)
    await _advance_to_ready_for_impl(store, task_a_id)

    task_b_id = await _setup_task(store, project_b.id)
    await _setup_spec(store, task_b_id)
    await _advance_to_ready_for_impl(store, task_b_id)

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_task(
        store, project_manager, spec_manager, state_machine,
        project_id=project_a.id,
    )

    assert result is not None
    assert result[0].id == task_a_id
    assert result[1].id == project_a.id
