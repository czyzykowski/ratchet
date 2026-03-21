"""Unit tests for worker.runner.merge_once using InMemoryStore and mocked squash_merge."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from core import events as ev
from core.merge import MergeResult
from core.project_manager import ProjectManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from worker.runner import merge_once

FAKE_REPO_PATH = "/fake/repo"
PATCH_SQUASH_MERGE = "worker.dispatcher.squash_merge"
PATCH_READ_INTENT = "worker.dispatcher.read_intent"
PATCH_LOAD_DEPLOYMENT = "worker.dispatcher.load_deployment_config"
PATCH_LOAD_MERGE_CONFIG = "worker.dispatcher.load_merge_config"
PATCH_RUN_MERGE_STEPS = "worker.dispatcher.run_merge_steps"


async def _setup_project(
    store: InMemoryStore,
    deployment_mode: str = "local",
    base_branch: str = "develop",
) -> tuple:
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
) -> uuid.UUID:
    """Create a task in READY_FOR_SPEC and register it under project_tasks."""
    task_id = uuid.uuid4()
    task_payload: dict = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "title": "Test task",
        "status": ev.READY_FOR_SPEC,
        "refinement_count": 0,
        "required_capabilities": [],
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


async def _advance_to_ready_for_deployment(
    store: InMemoryStore,
    task_id: uuid.UUID,
    branch_name: str = "task/exec-abc",
    spec_id: uuid.UUID | None = None,
) -> None:
    """Advance task to READY_FOR_DEPLOYMENT and add execution + spec events."""
    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_id, ev.SPEC_QA)
    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    await state_machine.transition(task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})
    await state_machine.transition(task_id, ev.READY_FOR_QA)
    await state_machine.transition(task_id, ev.READY_FOR_DEPLOYMENT)

    sid = spec_id or uuid.uuid4()
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload={"branch_name": branch_name, "spec_id": str(sid)},
    )
    await store.append_event(
        aggregate_id=sid,
        aggregate_type="spec",
        event_type=ev.SPEC_CREATED,
        payload={"content": "# Spec\nDo the thing.", "task_id": str(task_id)},
    )


def _make_invoker() -> MagicMock:
    return MagicMock()


def _local_deployment_config():
    from core.qa_runner import DeploymentConfig
    return DeploymentConfig(mode="local", base_branch="develop")


def _pr_deployment_config():
    from core.qa_runner import DeploymentConfig
    return DeploymentConfig(mode="pr", base_branch="develop")


class TestMergeOnceHappyPath:
    async def test_should_return_true_and_transition_to_deployed_on_success(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="abc123")

        with (
            patch(PATCH_SQUASH_MERGE, return_value=success_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            result = await merge_once(store, invoker)

        assert result is True
        state_machine = TaskStateMachine(store)
        status = await state_machine.get_current_status(task_id)
        assert status == ev.DEPLOYED

    async def test_should_pass_correct_branch_and_title_to_squash_merge(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id, branch_name="task/exec-xyz")

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="sha1")
        captured_args: list = []

        def capture_squash(*args, **kwargs):
            captured_args.extend(args)
            return success_result

        with (
            patch(PATCH_SQUASH_MERGE, side_effect=capture_squash),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            await merge_once(store, invoker)

        # squash_merge(local_path, execution_branch, target_branch, title, task_id, ...)
        assert captured_args[1] == "task/exec-xyz"  # execution_branch
        assert captured_args[2] == "develop"         # target_branch
        assert captured_args[4] == task_id           # task_id

    async def test_should_run_merge_hooks_and_record_event_on_success(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="abc123")

        from core.qa_runner import QaConfig, QaStepResult
        merge_config = QaConfig(steps=["git push"])
        hook_result = QaStepResult(
            step_name="git push", command="git push", returncode=0, output="ok"
        )

        with (
            patch(PATCH_SQUASH_MERGE, return_value=success_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=merge_config),
            patch(PATCH_RUN_MERGE_STEPS, return_value=[hook_result]),
        ):
            await merge_once(store, invoker)

        task_events = await store.get_events(task_id, "task")
        hook_events = [e for e in task_events if e.event_type == ev.TASK_DEPLOY_HOOKS_RUN]
        assert len(hook_events) == 1


class TestMergeOncePrMode:
    async def test_should_skip_pr_mode_project_and_return_false(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()

        with patch(PATCH_LOAD_DEPLOYMENT, return_value=_pr_deployment_config()):
            result = await merge_once(store, invoker)

        assert result is False
        state_machine = TaskStateMachine(store)
        status = await state_machine.get_current_status(task_id)
        assert status == ev.READY_FOR_DEPLOYMENT  # unchanged


class TestMergeOnceAutoMergeFailed:
    async def test_should_skip_task_with_existing_auto_merge_failed_event(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        # Record a prior failure
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_AUTO_MERGE_FAILED,
            payload={"failure_reason": "prior failure"},
        )

        invoker = _make_invoker()

        with (
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_SQUASH_MERGE) as mock_merge,
        ):
            result = await merge_once(store, invoker)

        assert result is False
        mock_merge.assert_not_called()


class TestMergeOnceNoTasks:
    async def test_should_return_false_when_no_tasks_in_ready_for_deployment(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        await _setup_task(store, project.id)
        # Task stays in READY_FOR_SPEC, not advanced

        invoker = _make_invoker()

        with patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()):
            result = await merge_once(store, invoker)

        assert result is False

    async def test_should_return_false_when_store_is_empty(self):
        store = InMemoryStore()
        invoker = _make_invoker()

        result = await merge_once(store, invoker)

        assert result is False


class TestMergeOnceMergeFailure:
    async def test_should_record_auto_merge_failed_event_on_failure(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        failure_result = MergeResult(success=False, failure_reason="CONFLICT in foo.py")

        with (
            patch(PATCH_SQUASH_MERGE, return_value=failure_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
        ):
            result = await merge_once(store, invoker)

        assert result is False
        task_events = await store.get_events(task_id, "task")
        failed_events = [e for e in task_events if e.event_type == ev.TASK_AUTO_MERGE_FAILED]
        assert len(failed_events) == 1
        assert "CONFLICT" in failed_events[0].payload.get("failure_reason", "")

    async def test_should_keep_task_in_ready_for_deployment_on_failure(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        failure_result = MergeResult(success=False, failure_reason="conflict")

        with (
            patch(PATCH_SQUASH_MERGE, return_value=failure_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
        ):
            await merge_once(store, invoker)

        state_machine = TaskStateMachine(store)
        status = await state_machine.get_current_status(task_id)
        assert status == ev.READY_FOR_DEPLOYMENT  # not BLOCKED


class TestMergeOnceReadIntentFailure:
    async def test_should_use_empty_intent_when_read_intent_raises(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="abc123")

        with (
            patch(PATCH_SQUASH_MERGE, return_value=success_result),
            patch(PATCH_READ_INTENT, side_effect=FileNotFoundError("no INTENT.md")),
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            result = await merge_once(store, invoker)

        assert result is True
        state_machine = TaskStateMachine(store)
        status = await state_machine.get_current_status(task_id)
        assert status == ev.DEPLOYED


class TestMergeOnceNoExecutionBranch:
    async def test_should_skip_task_with_no_execution_branch(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)

        # Advance to READY_FOR_DEPLOYMENT via state machine
        state_machine = TaskStateMachine(store)
        await state_machine.transition(task_id, ev.SPEC_QA)
        await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
        await state_machine.transition(
            task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
        )
        await state_machine.transition(task_id, ev.READY_FOR_QA)
        await state_machine.transition(task_id, ev.READY_FOR_DEPLOYMENT)

        # Append EXECUTION_STARTED with empty branch_name (falsy)
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_executions",
            event_type=ev.EXECUTION_STARTED,
            payload={"branch_name": "", "spec_id": str(uuid.uuid4())},
        )

        invoker = _make_invoker()

        with (
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_SQUASH_MERGE) as mock_merge,
        ):
            result = await merge_once(store, invoker)

        assert result is False
        mock_merge.assert_not_called()


class TestMergeOnceConfigSource:
    async def test_should_pass_ratchet_yaml_when_config_source_is_db(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="sha1")
        captured_kwargs: list = []

        def capture_load_deployment(local_path, ratchet_yaml=None):
            captured_kwargs.append(ratchet_yaml)
            return _local_deployment_config()

        # Build a modified project with config_source="db" and ratchet_yaml set
        modified_project = project.model_copy(
            update={"config_source": "db", "ratchet_yaml": "custom: yaml"}
        )

        with (
            patch("worker.dispatcher.ProjectManager") as mock_pm_cls,
            patch(PATCH_SQUASH_MERGE, return_value=success_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, side_effect=capture_load_deployment),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            mock_pm = MagicMock()
            mock_pm_cls.return_value = mock_pm
            mock_pm.list_projects = MagicMock(return_value=_async_return([modified_project]))
            result = await merge_once(store, invoker)

        assert result is True
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0] == "custom: yaml"

    async def test_should_pass_none_ratchet_yaml_when_config_source_is_not_db(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="sha1")
        captured_kwargs: list = []

        def capture_load_deployment(local_path, ratchet_yaml=None):
            captured_kwargs.append(ratchet_yaml)
            return _local_deployment_config()

        with (
            patch(PATCH_SQUASH_MERGE, return_value=success_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, side_effect=capture_load_deployment),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            result = await merge_once(store, invoker)

        assert result is True
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0] is None


class TestMergeOnceNoSpecId:
    async def test_should_use_empty_spec_content_when_no_spec_id(self):
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)

        # Advance to READY_FOR_DEPLOYMENT but append EXECUTION_STARTED without spec_id
        state_machine = TaskStateMachine(store)
        await state_machine.transition(task_id, ev.SPEC_QA)
        await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
        await state_machine.transition(
            task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
        )
        await state_machine.transition(task_id, ev.READY_FOR_QA)
        await state_machine.transition(task_id, ev.READY_FOR_DEPLOYMENT)

        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_executions",
            event_type=ev.EXECUTION_STARTED,
            payload={"branch_name": "task/exec-no-spec"},
            # no spec_id key
        )

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="sha1")
        captured_args: list = []

        def capture_squash(*args, **kwargs):
            captured_args.extend(args)
            return success_result

        with (
            patch(PATCH_SQUASH_MERGE, side_effect=capture_squash),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            result = await merge_once(store, invoker)

        assert result is True
        # squash_merge args: local_path, execution_branch, target_branch, title,
        # task_id, store, invoker, spec_content, intent_content
        spec_content_arg = captured_args[7]
        assert spec_content_arg == ""


def _async_return(value):
    """Helper to create an async coroutine returning value."""
    async def _inner():
        return value
    return _inner()


class TestMergeOnceMultipleTasks:
    async def test_should_merge_oldest_task_first(self):
        import asyncio

        store = InMemoryStore()
        _, project = await _setup_project(store)

        task_id_1 = await _setup_task(store, project.id)
        # Small sleep to ensure different created_at timestamps
        await asyncio.sleep(0.01)
        task_id_2 = await _setup_task(store, project.id)

        await _advance_to_ready_for_deployment(store, task_id_1, branch_name="task/branch-1")
        await _advance_to_ready_for_deployment(store, task_id_2, branch_name="task/branch-2")

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="sha")
        merged_task_ids: list[uuid.UUID] = []

        def capture_squash(*args, **kwargs):
            # squash_merge(local_path, execution_branch, target_branch, title, task_id, ...)
            merged_task_ids.append(args[4])
            return success_result

        with (
            patch(PATCH_SQUASH_MERGE, side_effect=capture_squash),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            result = await merge_once(store, invoker)

        assert result is True
        assert len(merged_task_ids) == 1
        assert merged_task_ids[0] == task_id_1  # oldest merged first
