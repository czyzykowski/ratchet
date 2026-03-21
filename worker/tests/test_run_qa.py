"""Unit tests for QA worker functions: get_next_qa_task and run_qa_once."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from core import events as ev
from core.project_manager import ProjectManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from worker.runner import get_next_qa_task, run_qa_once

FAKE_REPO_PATH = "/fake/repo"

PATCH_VALIDATE_REPO = "core.project_manager.validate_repo"


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
    required_capabilities: list[str] | None = None,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    task_payload = {
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


async def _advance_task_to_ready_for_qa(store: InMemoryStore, task_id: uuid.UUID) -> None:
    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_id, ev.SPEC_QA)
    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    await state_machine.transition(task_id, ev.IN_PROGRESS)
    await state_machine.transition(task_id, ev.READY_FOR_QA)


async def _setup_spec(store: InMemoryStore, task_id: uuid.UUID) -> uuid.UUID:
    spec_manager = SpecManager(store)
    spec = await spec_manager.create_spec(task_id, "# Spec content\nDo the thing.")
    await spec_manager.assign_spec(task_id, spec.id)
    return spec.id


# ---------------------------------------------------------------------------
# get_next_qa_task
# ---------------------------------------------------------------------------


async def test_get_next_qa_task_returns_none_when_no_tasks() -> None:
    store = InMemoryStore()
    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_qa_task(store, project_manager, spec_manager, state_machine)
    assert result is None


async def test_get_next_qa_task_returns_none_when_no_ready_for_qa_tasks() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id, initial_status=ev.READY_FOR_SPEC)
    await _setup_spec(store, task_id)

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_qa_task(store, project_manager, spec_manager, state_machine)
    assert result is None


async def test_get_next_qa_task_returns_ready_for_qa_task() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready_for_qa(store, task_id)

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_qa_task(store, project_manager, spec_manager, state_machine)
    assert result is not None
    task, project_out, spec = result
    assert task.id == task_id
    assert task.status == ev.READY_FOR_QA


async def test_get_next_qa_task_returns_oldest_when_multiple_exist() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)

    task_id_1 = await _setup_task(store, project.id)
    await _setup_spec(store, task_id_1)
    await _advance_task_to_ready_for_qa(store, task_id_1)

    task_id_2 = await _setup_task(store, project.id)
    await _setup_spec(store, task_id_2)
    await _advance_task_to_ready_for_qa(store, task_id_2)

    project_manager = ProjectManager(store)
    spec_manager = SpecManager(store)
    state_machine = TaskStateMachine(store)

    result = await get_next_qa_task(store, project_manager, spec_manager, state_machine)
    assert result is not None
    task, _, _ = result
    assert task.id == task_id_1


# ---------------------------------------------------------------------------
# run_qa_once
# ---------------------------------------------------------------------------


async def test_run_qa_once_transitions_to_ready_for_deployment_when_no_qa_config() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id)
    await _setup_spec(store, task_id)
    await _advance_task_to_ready_for_qa(store, task_id)

    with patch("worker.pipelines.qa.load_qa_config", return_value=None):
        await run_qa_once(store)

    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.READY_FOR_DEPLOYMENT


async def test_run_qa_once_does_nothing_when_no_qa_tasks() -> None:
    store = InMemoryStore()

    # Should not raise
    await run_qa_once(store)


# ---------------------------------------------------------------------------
# QAPipeline capability filtering
# ---------------------------------------------------------------------------


async def test_qa_pipeline_matches_task_with_no_required_capabilities() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id, required_capabilities=[])
    await _setup_spec(store, task_id)
    await _advance_task_to_ready_for_qa(store, task_id)

    with patch("worker.pipelines.qa.load_qa_config", return_value=None):
        result = await run_qa_once(store, local_capabilities=[])

    assert result is True
    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.READY_FOR_DEPLOYMENT


async def test_qa_pipeline_matches_task_with_matched_capabilities() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id, required_capabilities=["docker"])
    await _setup_spec(store, task_id)
    await _advance_task_to_ready_for_qa(store, task_id)

    with patch("worker.pipelines.qa.load_qa_config", return_value=None):
        result = await run_qa_once(store, local_capabilities=["docker", "gpu"])

    assert result is True
    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.READY_FOR_DEPLOYMENT


async def test_qa_pipeline_skips_task_with_unmatched_capabilities() -> None:
    store = InMemoryStore()
    _, project = await _setup_project(store)
    task_id = await _setup_task(store, project.id, required_capabilities=["gpu"])
    await _setup_spec(store, task_id)
    await _advance_task_to_ready_for_qa(store, task_id)

    result = await run_qa_once(store, local_capabilities=[])

    assert result is False
    state_machine = TaskStateMachine(store)
    status = await state_machine.get_current_status(task_id)
    assert status == ev.READY_FOR_QA
