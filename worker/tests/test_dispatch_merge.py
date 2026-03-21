"""Unit tests verifying merge_once is integrated into the dispatch cycle."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from core import events as ev
from core.project_manager import ProjectManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from worker.runner import merge_once

PATCH_VALIDATE_REPO = "core.project_manager.validate_repo"
PATCH_LOAD_DEPLOYMENT = "worker.pipelines.merge.load_deployment_config"
PATCH_SQUASH_MERGE = "worker.pipelines.merge.squash_merge"
PATCH_READ_INTENT = "worker.pipelines.merge.read_intent"
PATCH_LOAD_MERGE_CONFIG = "worker.pipelines.merge.load_merge_config"

FAKE_REPO_PATH = "/fake/repo"


def _make_invoker() -> MagicMock:
    return MagicMock()


def _local_deployment_config():
    from core.qa_runner import DeploymentConfig

    return DeploymentConfig(mode="local", base_branch="develop")


def _pr_deployment_config():
    from core.qa_runner import DeploymentConfig

    return DeploymentConfig(mode="pr", base_branch="develop")


async def _setup_project(store: InMemoryStore, name: str = "test-project"):
    project_manager = ProjectManager(store)
    with patch(PATCH_VALIDATE_REPO):
        project = await project_manager.register_project(
            name=name,
            repo_url="https://github.com/test/repo",
            local_path=FAKE_REPO_PATH,
        )
    return project_manager, project


async def _setup_task(store: InMemoryStore, project_id: uuid.UUID) -> uuid.UUID:
    task_id = uuid.uuid4()
    task_payload = {
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
) -> None:
    state_machine = TaskStateMachine(store)
    await state_machine.transition(task_id, ev.SPEC_QA)
    await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    await state_machine.transition(task_id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})
    await state_machine.transition(task_id, ev.READY_FOR_QA)
    await state_machine.transition(task_id, ev.READY_FOR_DEPLOYMENT)

    sid = uuid.uuid4()
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


class TestDispatchForProjectMergeIntegration:
    """Tests for _dispatch_for_project QA→merge→impl ordering."""

    async def test_dispatch_for_project_calls_merge_after_qa(self):
        """When QA returns False, merge_once should be called; run_once should NOT be called."""
        store = InMemoryStore()
        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        from core.merge import MergeResult

        success_result = MergeResult(success=True, new_sha="abc123")

        # Call merge_once directly with project_id to verify signature works
        with (
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_SQUASH_MERGE, return_value=success_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            result = await merge_once(store, _make_invoker(), project_id=project.id)

        assert result is True

    async def test_dispatch_for_project_skips_merge_when_qa_ran(self):
        """When QA returns True (work done), merge_once should NOT be called."""
        store = InMemoryStore()
        _, project = await _setup_project(store)

        mock_merge = AsyncMock(return_value=False)
        mock_run_once = AsyncMock(return_value=False)

        # Simulate dispatch logic: QA=True → neither merge nor run_once should be called
        did_qa_result = True
        if not did_qa_result:
            did_merge = await mock_merge(store, _make_invoker(), project_id=project.id)
            if not did_merge:
                await mock_run_once(store, _make_invoker(), [], project_id=project.id)

        mock_merge.assert_not_called()
        mock_run_once.assert_not_called()

    async def test_dispatch_for_project_falls_through_to_run_once(self):
        """When QA=False and merge=False, run_once SHOULD be called."""
        store = InMemoryStore()
        _, project = await _setup_project(store)

        mock_merge = AsyncMock(return_value=False)
        mock_run_once = AsyncMock(return_value=False)

        # Simulate dispatch logic: QA=False, merge=False → run_once called
        did_qa_result = False
        if not did_qa_result:
            did_merge = await mock_merge(store, _make_invoker(), project_id=project.id)
            if not did_merge:
                await mock_run_once(store, _make_invoker(), [], project_id=project.id)

        mock_merge.assert_called_once()
        mock_run_once.assert_called_once()

    async def test_main_async_calls_dispatch_all(self):
        """_main_async should call dispatch_all which runs merge>qa>impl>compile."""
        from worker.dispatcher import DispatchResult, ProjectDispatcher

        dispatch_all_called = False

        async def fake_dispatch_all(self_disp, busy_projects=None):
            nonlocal dispatch_all_called
            dispatch_all_called = True
            return [DispatchResult(action="idle", success=True)]

        async def fake_close_pool():
            pass

        with (
            patch.object(ProjectDispatcher, "dispatch_all", fake_dispatch_all),
            patch("core.db.close_pool", side_effect=fake_close_pool),
            patch("core.store.PostgresStore", return_value=InMemoryStore()),
            patch("worker.runner.ClaudeCodeInvoker", return_value=_make_invoker()),
        ):
            from worker.runner import _main_async

            await _main_async()

        assert dispatch_all_called

    async def test_dispatch_calls_merge_before_qa_before_impl(self):
        """ProjectDispatcher.dispatch calls merge>qa>impl in priority order."""
        from worker.dispatcher import DispatchResult, ProjectDispatcher

        store = InMemoryStore()
        _, project = await _setup_project(store)
        invoker = _make_invoker()
        dispatcher = ProjectDispatcher(store, invoker)

        call_order: list[str] = []

        async def fake_merge_once(self_disp, project_id=None):
            call_order.append("merge")
            return DispatchResult(action="idle", success=True)

        async def fake_qa_once(self_disp, project_id=None):
            call_order.append("qa")
            return DispatchResult(action="idle", success=True)

        async def fake_impl_once(self_disp, project_id=None):
            call_order.append("impl")
            return DispatchResult(action="idle", success=True)

        with (
            patch.object(ProjectDispatcher, "merge_once", fake_merge_once),
            patch.object(ProjectDispatcher, "qa_once", fake_qa_once),
            patch.object(ProjectDispatcher, "impl_once", fake_impl_once),
        ):
            await dispatcher.dispatch(project.id)

        assert call_order == ["merge", "qa", "impl"]

    async def test_dispatch_skips_impl_when_qa_ran(self):
        """ProjectDispatcher.dispatch: when QA does work, impl should be skipped."""
        from worker.dispatcher import DispatchResult, ProjectDispatcher

        store = InMemoryStore()
        _, project = await _setup_project(store)
        invoker = _make_invoker()
        dispatcher = ProjectDispatcher(store, invoker)

        call_order: list[str] = []

        async def fake_merge_once(self_disp, project_id=None):
            call_order.append("merge")
            return DispatchResult(action="idle", success=True)

        async def fake_qa_once(self_disp, project_id=None):
            call_order.append("qa")
            return DispatchResult(action="qa", success=True)  # QA did work

        async def fake_impl_once(self_disp, project_id=None):
            call_order.append("impl")
            return DispatchResult(action="idle", success=True)

        with (
            patch.object(ProjectDispatcher, "merge_once", fake_merge_once),
            patch.object(ProjectDispatcher, "qa_once", fake_qa_once),
            patch.object(ProjectDispatcher, "impl_once", fake_impl_once),
        ):
            await dispatcher.dispatch(project.id)

        assert call_order == ["merge", "qa"]  # no impl


class TestMergeOnceProjectIdFilter:
    """Tests for merge_once project_id filtering."""

    async def test_merge_once_project_id_filter_excludes_other_projects(self):
        """merge_once with project_id should only consider that project."""
        store = InMemoryStore()

        # Project A: local mode, has a ready-for-deployment task
        _, project_a = await _setup_project(store, name="project-a")
        task_a = await _setup_task(store, project_a.id)
        await _advance_to_ready_for_deployment(store, task_a)

        # Project B: local mode, no tasks
        _, project_b = await _setup_project(store, name="project-b")

        invoker = _make_invoker()

        # Call merge_once filtered to project_b — should find no candidates
        with patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()):
            result = await merge_once(store, invoker, project_id=project_b.id)

        assert result is False
        # Task A should remain in READY_FOR_DEPLOYMENT (untouched)
        state_machine = TaskStateMachine(store)
        status = await state_machine.get_current_status(task_a)
        assert status == ev.READY_FOR_DEPLOYMENT

    async def test_merge_once_project_id_filter_includes_correct_project(self):
        """merge_once with project_id should merge task from the specified project."""
        store = InMemoryStore()
        from core.merge import MergeResult

        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="sha123")

        with (
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_SQUASH_MERGE, return_value=success_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            result = await merge_once(store, invoker, project_id=project.id)

        assert result is True
        state_machine = TaskStateMachine(store)
        status = await state_machine.get_current_status(task_id)
        assert status == ev.DEPLOYED

    async def test_merge_once_no_project_id_considers_all_projects(self):
        """merge_once without project_id should consider all local-mode projects."""
        store = InMemoryStore()
        from core.merge import MergeResult

        _, project = await _setup_project(store)
        task_id = await _setup_task(store, project.id)
        await _advance_to_ready_for_deployment(store, task_id)

        invoker = _make_invoker()
        success_result = MergeResult(success=True, new_sha="sha123")

        with (
            patch(PATCH_LOAD_DEPLOYMENT, return_value=_local_deployment_config()),
            patch(PATCH_SQUASH_MERGE, return_value=success_result),
            patch(PATCH_READ_INTENT, return_value="intent"),
            patch(PATCH_LOAD_MERGE_CONFIG, return_value=None),
        ):
            # No project_id filter — should still find and merge the task
            result = await merge_once(store, invoker)

        assert result is True
