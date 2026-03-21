"""Tests for Managers facade."""

from __future__ import annotations

from unittest.mock import patch

from core.managers import Managers
from core.store import InMemoryStore


async def test_task_created_through_managers_is_retrievable() -> None:
    store = InMemoryStore()
    m = Managers(store)

    with patch("core.project_manager.validate_repo"):
        project = await m.projects.register_project(
            name="test", repo_url="https://example.com", local_path="/tmp/test"
        )

    task = await m.tasks.create_task(project.id, "My task")
    retrieved = await m.tasks.get_task(task.id)

    assert retrieved is not None
    assert retrieved.title == "My task"


async def test_state_machine_transitions_task_created_by_same_managers() -> None:
    """Managers share the same store — state_machine sees tasks created by tasks manager."""
    from core import events as ev

    store = InMemoryStore()
    m = Managers(store)

    with patch("core.project_manager.validate_repo"):
        project = await m.projects.register_project(
            name="test", repo_url="https://example.com", local_path="/tmp/test"
        )

    task = await m.tasks.create_task(project.id, "My task")
    await m.state_machine.transition(task.id, ev.SPEC_QA)
    await m.state_machine.transition(task.id, ev.READY_FOR_IMPLEMENTATION)

    updated = await m.tasks.get_task(task.id)
    assert updated is not None
    assert updated.status == ev.READY_FOR_IMPLEMENTATION


async def test_execution_factory_returns_manager_with_correct_path() -> None:
    from core.execution_manager import ExecutionManager

    store = InMemoryStore()
    m = Managers(store)
    em = m.execution("/my/repo")

    assert isinstance(em, ExecutionManager)
    assert em._store is store
    assert em._repo_path == "/my/repo"
