"""Tests for continuous loop behaviour: return values and notification_loop control flow."""

from __future__ import annotations

import asyncio
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
from worker.runner import notification_loop, run_once, run_qa_once

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

    All three return False so all run during catchup (requires at least one project).
    """
    store = InMemoryStore()
    invoker = _make_invoker()

    # Register a project so per-project dispatch fires run_qa_once and run_once
    _, project = await _setup_project(store)

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
    """notification_loop dispatches per-project when notifications arrive.

    Uses a persistent listener (doesn't exit on its own) and a background task so we
    can observe N dispatch rounds before cancelling the loop.
    """
    store = InMemoryStore()
    invoker = _make_invoker()

    # Register a project so per-project dispatch fires
    _, project = await _setup_project(store)

    task_id = str(uuid.uuid4())
    dispatch_calls: list[str] = []
    enough = asyncio.Event()

    async def fake_compile_once(*args, **kwargs) -> bool:
        dispatch_calls.append("compile_once")
        return False

    async def fake_run_once(*args, **kwargs) -> bool:
        dispatch_calls.append("run_once")
        return True

    async def fake_run_qa_once(*args, **kwargs) -> bool:
        dispatch_calls.append("run_qa_once")
        if dispatch_calls.count("run_qa_once") >= 2:
            enough.set()
        return False

    class _PersistentListener(_MockNotificationListener):
        """Yields notifications then stays alive until cancelled."""
        async def listen(self) -> AsyncGenerator[tuple[str, str, str], None]:
            for item in self._notifications:
                yield item
            while True:
                await asyncio.sleep(10)

    notifications = [
        ("task", task_id, ev.READY_FOR_IMPLEMENTATION),
        ("task", task_id, ev.READY_FOR_QA),
    ]
    mock_listener = _PersistentListener(notifications)

    with (
        patch("worker.runner.compile_once", side_effect=fake_compile_once),
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.run_qa_once", side_effect=fake_run_qa_once),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        loop_task = asyncio.create_task(
            notification_loop(store, invoker, dsn="postgresql://fake/test")
        )
        try:
            await asyncio.wait_for(enough.wait(), timeout=5.0)
        finally:
            loop_task.cancel()
            try:
                await loop_task
            except (asyncio.CancelledError, Exception):
                pass

    # Startup + at least one notification round
    assert dispatch_calls.count("run_qa_once") >= 2
    assert dispatch_calls.count("run_once") >= 2


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


async def _setup_project_named(store: InMemoryStore, name: str, path: str) -> tuple:
    """Register a named project at a given path."""
    project_manager = ProjectManager(store)
    with patch(PATCH_VALIDATE_REPO):
        project = await project_manager.register_project(
            name=name,
            repo_url=f"https://github.com/test/{name}",
            local_path=path,
        )
    return project_manager, project


async def test_two_projects_dispatch_concurrently() -> None:
    """Two projects each with a ready_for_implementation task both get dispatched in one round."""
    store = InMemoryStore()
    invoker = _make_invoker()

    _, project_a = await _setup_project_named(store, "project-a", "/fake/a")
    _, project_b = await _setup_project_named(store, "project-b", "/fake/b")

    dispatched_project_ids: list[uuid.UUID | None] = []

    async def fake_run_qa_once(store, invoker=None, project_id=None):
        dispatched_project_ids.append(project_id)
        return False

    async def fake_run_once(store, invoker=None, local_capabilities=[], project_id=None):
        return False

    mock_listener = _MockNotificationListener([])

    with (
        patch("worker.runner.run_qa_once", side_effect=fake_run_qa_once),
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.compile_once", return_value=False),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(store, invoker, dsn="postgresql://fake/test")

    # Both projects should have been dispatched in the startup catchup round
    assert project_a.id in dispatched_project_ids
    assert project_b.id in dispatched_project_ids


async def test_busy_project_skipped() -> None:
    """Project already in busy_projects is not dispatched during that round."""
    store = InMemoryStore()
    invoker = _make_invoker()

    _, project_a = await _setup_project_named(store, "project-a", "/fake/a")

    dispatched_project_ids: list[uuid.UUID | None] = []

    async def fake_run_qa_once(store, invoker=None, project_id=None):
        dispatched_project_ids.append(project_id)
        return False

    async def fake_run_once(store, invoker=None, local_capabilities=[], project_id=None):
        return False

    mock_listener = _MockNotificationListener([])

    # Pre-seed busy_projects with project_a's ID — it should be skipped during dispatch
    pre_busy: set[uuid.UUID] = {project_a.id}

    with (
        patch("worker.runner.run_qa_once", side_effect=fake_run_qa_once),
        patch("worker.runner.run_once", side_effect=fake_run_once),
        patch("worker.runner.compile_once", return_value=False),
        patch("worker.runner.NotificationListener", return_value=mock_listener),
    ):
        await notification_loop(
            store, invoker, dsn="postgresql://fake/test", _initial_busy_projects=pre_busy
        )

    # project_a was busy — must not appear in dispatched calls
    assert project_a.id not in dispatched_project_ids
