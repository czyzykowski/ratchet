"""Tests for PipelineSequencer.run_merge_pipeline() — remote worker merge."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from core import events as ev
from core.merge import MergeResult
from core.project_manager import ProjectManager
from core.remote_protocol import (
    CreateWorktreeResponse,
    GetProjectStatusResponse,
    ReadFileResponse,
    RemoveWorktreeResponse,
    RunCommandResponse,
    SetupEnvironmentResponse,
)
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.channel import PipelineAbort
from orchestrator.sequencer import PipelineSequencer


class MockChannel:
    def __init__(self, handler, worker_id: str = "test-worker") -> None:
        self._handler = handler
        self._worker_id = worker_id
        self.sent_requests: list = []

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def send_command(self, request):
        self.sent_requests.append(request)
        return self._handler(request)


async def _seed_merge_task(store: InMemoryStore):
    """Register project, create task + spec, transition to ready_for_deployment."""
    with patch("core.project_manager.validate_repo"):
        project = await ProjectManager(store).register_project(
            name="test-project",
            repo_url="http://fake",
            local_path="/fake/path",
            config_source="db",
        )

    task = await TaskManager(store).create_task(project.id, "Merge task")
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task.id, ev.IN_PROGRESS)

    execution_id = uuid4()
    branch_name = f"execution/{execution_id}"
    payload = {
        "execution_id": str(execution_id),
        "task_id": str(task.id),
        "spec_id": str(spec.id),
        "worktree_path": f"remote/{execution_id}",
        "branch_name": branch_name,
        "status": "running",
    }
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task_executions",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )

    await sm.transition(task.id, ev.READY_FOR_QA)
    await sm.transition(task.id, ev.READY_FOR_DEPLOYMENT)

    task = await TaskManager(store).get_task(task.id)
    assert task is not None
    return task, project, spec, branch_name


def _make_handler(*, pass_qa: bool = True):
    def handler(req):
        t = req.type
        if t == "get_project_status":
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=req.request_id,
                success=True,
                exists=True,
                head_commit="dev-sha",
            )
        if t == "create_worktree":
            return CreateWorktreeResponse(
                type="create_worktree_response",
                request_id=req.request_id,
                success=True,
                worktree_path="/remote/merge-wt",
            )
        if t == "setup_environment":
            return SetupEnvironmentResponse(
                type="setup_environment_response",
                request_id=req.request_id,
                success=True,
            )
        if t == "read_file":
            return ReadFileResponse(
                type="read_file_response",
                request_id=req.request_id,
                success=True,
                content=None,
            )
        if t == "run_command":
            rc = 0 if pass_qa else 1
            return RunCommandResponse(
                type="run_command_response",
                request_id=req.request_id,
                success=True,
                returncode=rc,
                stdout="ok" if pass_qa else "FAIL",
                stderr="",
            )
        if t == "remove_worktree":
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=req.request_id,
                success=True,
            )
        raise AssertionError(f"Unexpected request type: {t!r}")

    return handler


_RATCHET_YAML = "qa:\n  steps:\n    test: pytest\n"


@pytest.mark.asyncio
async def test_merge_pipeline_happy_path_transitions_to_deployed() -> None:
    store = InMemoryStore()
    task, project, spec, branch = await _seed_merge_task(store)

    channel = MockChannel(_make_handler(pass_qa=True))
    sequencer = PipelineSequencer(store)
    project.ratchet_yaml = _RATCHET_YAML

    fake_apply = MergeResult(success=True, new_sha="merged-sha")
    fake_diff = MagicMock(returncode=0, stdout="diff --git a/f.txt b/f.txt\n+new\n")

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="dev-sha"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
        patch("orchestrator.sequencer.apply_patch_to_develop", return_value=fake_apply),
        patch("orchestrator.sequencer._push_branch"),
    ):
        result = await sequencer.run_merge_pipeline(channel, task, project)

    assert result.success is True
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.DEPLOYED

    # Verify CreateWorktree received the patch
    create_wt = [r for r in channel.sent_requests if r.type == "create_worktree"]
    assert len(create_wt) == 1
    assert create_wt[0].patch is not None


@pytest.mark.asyncio
async def test_merge_pipeline_qa_failure_blocks_without_local_changes() -> None:
    store = InMemoryStore()
    task, project, spec, branch = await _seed_merge_task(store)

    channel = MockChannel(_make_handler(pass_qa=False))
    sequencer = PipelineSequencer(store)
    project.ratchet_yaml = _RATCHET_YAML

    fake_diff = MagicMock(returncode=0, stdout="diff content")

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="dev-sha"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
        patch("orchestrator.sequencer.apply_patch_to_develop") as apply_mock,
        patch("orchestrator.sequencer._push_branch") as push_mock,
    ):
        result = await sequencer.run_merge_pipeline(channel, task, project)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED
    # Local develop should NOT be modified
    apply_mock.assert_not_called()
    push_mock.assert_not_called()


@pytest.mark.asyncio
async def test_merge_pipeline_missing_execution_branch_blocks() -> None:
    store = InMemoryStore()
    task, project, spec, branch = await _seed_merge_task(store)

    channel = MockChannel(_make_handler(pass_qa=True))
    sequencer = PipelineSequencer(store)

    # git diff returns error (branch doesn't exist)
    fake_diff = MagicMock(returncode=128, stdout="", stderr="fatal: bad revision")

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="dev-sha"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
    ):
        result = await sequencer.run_merge_pipeline(channel, task, project)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED
    assert "execution branch" in (result.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_merge_pipeline_worker_abort_blocks() -> None:
    store = InMemoryStore()
    task, project, spec, branch = await _seed_merge_task(store)

    def handler(req):
        if req.type == "get_project_status":
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=req.request_id,
                success=True,
                exists=True,
                head_commit="dev-sha",
            )
        if req.type == "create_worktree":
            raise PipelineAbort("create_worktree", "network error", req.request_id)
        if req.type == "remove_worktree":
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=req.request_id,
                success=True,
            )
        raise AssertionError(f"Unexpected: {req.type!r}")

    channel = MockChannel(handler)
    sequencer = PipelineSequencer(store)

    fake_diff = MagicMock(returncode=0, stdout="diff content")

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="dev-sha"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
        patch("orchestrator.sequencer._push_branch"),
    ):
        result = await sequencer.run_merge_pipeline(channel, task, project)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED
