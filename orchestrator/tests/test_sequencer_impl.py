"""Tests for PipelineSequencer.run_impl_pipeline()."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core import events as ev
from core.models import Project, Spec, Task
from core.project_manager import ProjectManager
from core.remote_protocol import (
    CreateWorktreeResponse,
    GetDiffResponse,
    GetProjectStatusResponse,
    RemoveWorktreeResponse,
    RunClaudeResponse,
    SetupEnvironmentResponse,
    SetupProjectResponse,
)
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from orchestrator.channel import PipelineAbort
from orchestrator.sequencer import PipelineSequencer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockChannel:
    """Mock WorkerChannel that pops canned responses in order."""

    def __init__(self, responses: list, worker_id: str = "test-worker") -> None:
        self._responses = list(responses)
        self._worker_id = worker_id
        self.sent_requests: list = []

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def send_command(self, request):
        self.sent_requests.append(request)
        if not self._responses:
            raise AssertionError(
                f"MockChannel ran out of responses; got unexpected request {request.type!r}"
            )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


async def _seed_task_with_spec(
    store: InMemoryStore,
) -> tuple[Task, Project, Spec]:
    """Register project, create task + spec, transition to ready_for_implementation."""
    with patch("core.project_manager.validate_repo"):
        project = await ProjectManager(store).register_project(
            name="test-project",
            repo_url="http://fake",
            local_path="/fake/path",
            config_source="db",
        )

    task = await TaskManager(store).create_task(project.id, "Test task")
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    task = await TaskManager(store).get_task(task.id)
    assert task is not None
    return task, project, spec


def _status_response(request_id: str, *, exists: bool = True) -> GetProjectStatusResponse:
    return GetProjectStatusResponse(
        type="get_project_status_response",
        request_id=request_id,
        success=True,
        exists=exists,
        head_commit="abc123" if exists else None,
    )


def _create_wt_response(request_id: str) -> CreateWorktreeResponse:
    return CreateWorktreeResponse(
        type="create_worktree_response",
        request_id=request_id,
        success=True,
        worktree_path="/remote/wt",
    )


def _setup_env_response(request_id: str) -> SetupEnvironmentResponse:
    return SetupEnvironmentResponse(
        type="setup_environment_response",
        request_id=request_id,
        success=True,
    )


def _run_claude_response(request_id: str, *, stdout: str = "COMPLETED: done") -> RunClaudeResponse:
    return RunClaudeResponse(
        type="run_claude_response",
        request_id=request_id,
        success=True,
        status="completed",
        stdout=stdout,
        returncode=0,
    )


def _get_diff_response(request_id: str, *, patch: str = "diff...") -> GetDiffResponse:
    return GetDiffResponse(
        type="get_diff_response",
        request_id=request_id,
        success=True,
        patch=patch,
    )


def _remove_wt_response(request_id: str) -> RemoveWorktreeResponse:
    return RemoveWorktreeResponse(
        type="remove_worktree_response",
        request_id=request_id,
        success=True,
    )


def _make_channel_responses(*, claude_stdout: str = "COMPLETED: done") -> list:
    """Build canned response list for the happy path."""
    return [
        # GetProjectStatus
        lambda req: _status_response(req.request_id),
        # CreateWorktree
        lambda req: _create_wt_response(req.request_id),
        # SetupEnvironment
        lambda req: _setup_env_response(req.request_id),
        # RunClaude
        lambda req: _run_claude_response(req.request_id, stdout=claude_stdout),
        # GetDiff
        lambda req: _get_diff_response(req.request_id),
        # RemoveWorktree
        lambda req: _remove_wt_response(req.request_id),
    ]


class LambdaMockChannel(MockChannel):
    """Mock channel where responses can be callables taking the request."""

    async def send_command(self, request):
        self.sent_requests.append(request)
        if not self._responses:
            raise AssertionError(
                f"MockChannel ran out of responses; got unexpected request {request.type!r}"
            )
        entry = self._responses.pop(0)
        if isinstance(entry, Exception):
            raise entry
        if callable(entry):
            response = entry(request)
            if isinstance(response, Exception):
                raise response
            return response
        return entry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_impl_pipeline_happy_path_transitions_to_ready_for_qa() -> None:
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    channel = LambdaMockChannel(_make_channel_responses())
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is True
    assert result.task_id == task.id
    assert result.execution_id is not None

    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.READY_FOR_QA


@pytest.mark.asyncio
async def test_impl_pipeline_records_execution_started_event() -> None:
    """run_impl_pipeline records EXECUTION_STARTED on the task_executions aggregate.

    Note: TASK_ASSIGNED_TO_WORKER is recorded by the dispatcher, not the pipeline.
    """
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    channel = LambdaMockChannel(_make_channel_responses())
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        await sequencer.run_impl_pipeline(channel, task, project, spec)

    exec_events = await store.get_events(task.id, "task_executions")
    event_types = [e.event_type for e in exec_events]
    assert ev.EXECUTION_STARTED in event_types


@pytest.mark.asyncio
async def test_impl_pipeline_project_missing_sends_setup_project() -> None:
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    def responses(req):
        t = req.type
        if t == "get_project_status":
            # First call: not exists
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=req.request_id,
                success=True,
                exists=False,
            )
        if t == "setup_project":
            return SetupProjectResponse(
                type="setup_project_response",
                request_id=req.request_id,
                success=True,
            )
        if t == "create_worktree":
            return _create_wt_response(req.request_id)
        if t == "setup_environment":
            return _setup_env_response(req.request_id)
        if t == "run_claude":
            return _run_claude_response(req.request_id)
        if t == "get_diff":
            return _get_diff_response(req.request_id)
        if t == "remove_worktree":
            return _remove_wt_response(req.request_id)
        raise AssertionError(f"Unexpected request type: {t!r}")

    # We need a second GetProjectStatus call after SetupProject
    call_count: dict[str, int] = {"get_project_status": 0}

    class SmartMockChannel:
        worker_id = "test-worker"
        sent_requests: list = []

        async def send_command(self, request):
            self.sent_requests.append(request)
            if request.type == "get_project_status":
                call_count["get_project_status"] += 1
                if call_count["get_project_status"] == 1:
                    return GetProjectStatusResponse(
                        type="get_project_status_response",
                        request_id=request.request_id,
                        success=True,
                        exists=False,
                    )
                else:
                    return GetProjectStatusResponse(
                        type="get_project_status_response",
                        request_id=request.request_id,
                        success=True,
                        exists=True,
                        head_commit="new123",
                    )
            return responses(request)

    channel = SmartMockChannel()
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._create_patch_bundle_b64", return_value="bundle64"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is True
    sent_types = [r.type for r in channel.sent_requests]
    assert "setup_project" in sent_types


@pytest.mark.asyncio
async def test_impl_pipeline_blocked_output_transitions_to_blocked() -> None:
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    channel = LambdaMockChannel(
        _make_channel_responses(claude_stdout="BLOCKED: something went wrong")
    )
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED


@pytest.mark.asyncio
async def test_impl_pipeline_mid_pipeline_failure_removes_worktree_and_blocks() -> None:
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    # CreateWorktree will fail
    class FailingChannel:
        worker_id = "test-worker"
        sent_requests: list = []
        call_num = 0

        async def send_command(self, request):
            self.sent_requests.append(request)
            self.call_num += 1
            if request.type == "get_project_status":
                return GetProjectStatusResponse(
                    type="get_project_status_response",
                    request_id=request.request_id,
                    success=True,
                    exists=True,
                    head_commit="abc123",
                )
            if request.type == "create_worktree":
                raise PipelineAbort("create_worktree", "disk full", request.request_id)
            if request.type == "remove_worktree":
                return _remove_wt_response(request.request_id)
            raise AssertionError(f"Unexpected request: {request.type!r}")

    channel = FailingChannel()
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
    ):
        result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is False
    assert result.failure_reason is not None
    assert "create_worktree" in result.failure_reason

    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED


@pytest.mark.asyncio
async def test_impl_pipeline_empty_diff_blocks_task() -> None:
    """When impl completes but GetDiff returns empty patch, task is blocked."""
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    # Empty patch in GetDiff response
    responses = _make_channel_responses(claude_stdout="COMPLETED: done")
    # Replace the GetDiff lambda to return empty patch
    responses[4] = lambda req: GetDiffResponse(
        type="get_diff_response",
        request_id=req.request_id,
        success=True,
        patch="",
    )

    channel = LambdaMockChannel(responses)
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED


@pytest.mark.asyncio
async def test_apply_patch_to_local_raises_on_git_failure() -> None:
    """_apply_patch_to_local raises RuntimeError when git commands fail."""
    from unittest.mock import MagicMock

    with patch("orchestrator.sequencer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stderr="fatal: bad revision", stdout=""
        )
        with pytest.raises(RuntimeError, match="git worktree add failed"):
            PipelineSequencer._apply_patch_to_local(
                "/fake/path", "execution/test-branch", "diff content", "abc123"
            )
