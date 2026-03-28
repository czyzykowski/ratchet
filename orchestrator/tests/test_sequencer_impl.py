"""Tests for PipelineSequencer.run_impl_pipeline()."""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from core import events as ev
from core.models import Project, Spec, Task
from core.project_manager import ProjectManager
from core.remote_protocol import (
    CreateWorktreeResponse,
    GetDiffResponse,
    GetProjectStatusResponse,
    ReadFileResponse,
    RemoveWorktreeResponse,
    RunClaudeResponse,
    RunCommandResponse,
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
# Constants
# ---------------------------------------------------------------------------

_RATCHET_YAML = textwrap.dedent("""\
qa:
  steps:
    test: pytest
    lint: ruff check .
  max_fix_attempts: 2
""")

_RATCHET_YAML_ONE_STEP = textwrap.dedent("""\
qa:
  steps:
    test: pytest
  max_fix_attempts: 1
""")

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
    await sm.transition(task.id, ev.IN_PROGRESS)

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


def _read_file_response(
    request_id: str, *, content: str | None = _RATCHET_YAML
) -> ReadFileResponse:
    return ReadFileResponse(
        type="read_file_response",
        request_id=request_id,
        success=True,
        content=content,
    )


def _run_command_response(request_id: str, *, returncode: int = 0) -> RunCommandResponse:
    return RunCommandResponse(
        type="run_command_response",
        request_id=request_id,
        success=True,
        returncode=returncode,
        stdout="ok" if returncode == 0 else "FAILED",
        stderr="",
    )


def _review_claude_response(
    request_id: str, *, stdout: str = "QA_PASSED: all checks pass"
) -> RunClaudeResponse:
    return RunClaudeResponse(
        type="run_claude_response",
        request_id=request_id,
        success=True,
        status="completed",
        stdout=stdout,
        returncode=0,
    )


def _make_channel_responses(*, claude_stdout: str = "COMPLETED: done") -> list:
    """Build canned response list for the happy path (impl + QA passes + review passes)."""
    return [
        # GetProjectStatus
        lambda req: _status_response(req.request_id),
        # CreateWorktree
        lambda req: _create_wt_response(req.request_id),
        # SetupEnvironment
        lambda req: _setup_env_response(req.request_id),
        # RunClaude (impl)
        lambda req: _run_claude_response(req.request_id, stdout=claude_stdout),
        # ReadFile (ratchet.yaml)
        lambda req: _read_file_response(req.request_id),
        # RunCommand (test step)
        lambda req: _run_command_response(req.request_id),
        # RunCommand (lint step)
        lambda req: _run_command_response(req.request_id),
        # GetDiff (after QA passes)
        lambda req: _get_diff_response(req.request_id),
        # RunClaude (review)
        lambda req: _review_claude_response(req.request_id),
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
async def test_impl_pipeline_happy_path_transitions_to_ready_for_deployment() -> None:
    """Successful impl + QA pass → task reaches ready_for_deployment."""
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
    assert status == ev.READY_FOR_DEPLOYMENT


@pytest.mark.asyncio
async def test_impl_pipeline_records_execution_started_event() -> None:
    """run_impl_pipeline records EXECUTION_STARTED on the task_executions aggregate."""
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

    call_count: dict[str, int] = {"get_project_status": 0, "run_claude": 0}

    class SmartMockChannel:
        worker_id = "test-worker"
        sent_requests: list = []

        async def send_command(self, request):
            self.sent_requests.append(request)
            t = request.type
            if t == "get_project_status":
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
            if t == "setup_project":
                return SetupProjectResponse(
                    type="setup_project_response",
                    request_id=request.request_id,
                    success=True,
                )
            if t == "create_worktree":
                return _create_wt_response(request.request_id)
            if t == "setup_environment":
                return _setup_env_response(request.request_id)
            if t == "run_claude":
                call_count["run_claude"] += 1
                # First run_claude is impl, subsequent are review (or fix)
                if call_count["run_claude"] == 1:
                    return _run_claude_response(request.request_id)
                else:
                    return _review_claude_response(request.request_id)
            if t == "read_file":
                return _read_file_response(request.request_id)
            if t == "run_command":
                return _run_command_response(request.request_id)
            if t == "get_diff":
                return _get_diff_response(request.request_id)
            if t == "remove_worktree":
                return _remove_wt_response(request.request_id)
            raise AssertionError(f"Unexpected request type: {t!r}")

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
    """When no QA config and GetDiff returns empty patch, task is blocked."""
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    responses: list = [
        # GetProjectStatus
        lambda req: _status_response(req.request_id),
        # CreateWorktree
        lambda req: _create_wt_response(req.request_id),
        # SetupEnvironment
        lambda req: _setup_env_response(req.request_id),
        # RunClaude (impl, COMPLETED)
        lambda req: _run_claude_response(req.request_id),
        # ReadFile — no ratchet.yaml
        lambda req: _read_file_response(req.request_id, content=None),
        # GetDiff — empty patch
        lambda req: _get_diff_response(req.request_id, patch=""),
        # RemoveWorktree
        lambda req: _remove_wt_response(req.request_id),
    ]

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


@pytest.mark.asyncio
async def test_impl_pipeline_no_qa_config_transitions_to_ready_for_deployment() -> None:
    """When ratchet.yaml is missing, impl goes straight to ready_for_deployment."""
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    responses: list = [
        lambda req: _status_response(req.request_id),
        lambda req: _create_wt_response(req.request_id),
        lambda req: _setup_env_response(req.request_id),
        lambda req: _run_claude_response(req.request_id),
        # ReadFile returns no content
        lambda req: _read_file_response(req.request_id, content=None),
        # GetDiff
        lambda req: _get_diff_response(req.request_id),
        # RemoveWorktree
        lambda req: _remove_wt_response(req.request_id),
    ]

    channel = LambdaMockChannel(responses)
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is True
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.READY_FOR_DEPLOYMENT

    # Verify no run_command requests were sent (no QA ran)
    sent_types = [r.type for r in channel.sent_requests]
    assert "run_command" not in sent_types


@pytest.mark.asyncio
async def test_impl_pipeline_qa_passes_first_try_transitions_to_ready_for_deployment() -> None:
    """Impl succeeds + QA passes → ready_for_deployment (same as happy path)."""
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
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.READY_FOR_DEPLOYMENT

    # Verify QA steps were run
    sent_types = [r.type for r in channel.sent_requests]
    assert "run_command" in sent_types
    assert sent_types.count("run_command") == 2  # both QA steps


@pytest.mark.asyncio
async def test_impl_pipeline_qa_fails_fix_loop_then_passes() -> None:
    """QA fails once, fix is applied, QA passes → ready_for_deployment."""
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    run_command_call = 0

    def _smart_run_command(req):
        nonlocal run_command_call
        run_command_call += 1
        # First call (qa_run 1): fail
        if run_command_call == 1:
            return RunCommandResponse(
                type="run_command_response",
                request_id=req.request_id,
                success=True,
                returncode=1,
                stdout="test failed",
                stderr="",
            )
        # Subsequent calls (qa_run 2): pass
        return _run_command_response(req.request_id)

    responses: list = [
        lambda req: _status_response(req.request_id),
        lambda req: _create_wt_response(req.request_id),
        lambda req: _setup_env_response(req.request_id),
        lambda req: _run_claude_response(req.request_id),
        # ReadFile (ratchet.yaml with max_fix_attempts: 2)
        lambda req: _read_file_response(req.request_id),
        # qa_run 1: test fails
        _smart_run_command,
        # RunClaude (fix attempt 1)
        lambda req: _run_claude_response(req.request_id, stdout="fixed the issue"),
        # qa_run 2: test passes
        _smart_run_command,
        # qa_run 2: lint passes
        _smart_run_command,
        # GetDiff (after QA passes)
        lambda req: _get_diff_response(req.request_id),
        # RunClaude (review, passes)
        lambda req: _review_claude_response(req.request_id),
        # RemoveWorktree
        lambda req: _remove_wt_response(req.request_id),
    ]

    channel = LambdaMockChannel(responses)
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
        patch(
            "orchestrator.sequencer.classify_qa_failure",
            return_value="code",
            create=True,
        ),
    ):
        # Patch the local import inside the method
        with patch(
            "orchestrator.failure_classifier.classify_qa_failure",
            return_value="code",
        ):
            result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is True
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.READY_FOR_DEPLOYMENT

    # Verify fix attempt was sent
    sent_types = [r.type for r in channel.sent_requests]
    assert sent_types.count("run_claude") == 3  # impl + fix + review


@pytest.mark.asyncio
async def test_impl_pipeline_qa_fails_max_retries_transitions_to_blocked() -> None:
    """QA fails and all fix attempts exhausted → task blocked."""
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    # Use yaml with max_fix_attempts: 1 — one fix attempt, then block
    responses: list = [
        lambda req: _status_response(req.request_id),
        lambda req: _create_wt_response(req.request_id),
        lambda req: _setup_env_response(req.request_id),
        lambda req: _run_claude_response(req.request_id),
        # ReadFile (max_fix_attempts: 1)
        lambda req: _read_file_response(req.request_id, content=_RATCHET_YAML_ONE_STEP),
        # qa_run 1: test fails
        lambda req: _run_command_response(req.request_id, returncode=1),
        # RunClaude (fix attempt 1)
        lambda req: _run_claude_response(req.request_id, stdout="fix attempt"),
        # qa_run 2: test fails again
        lambda req: _run_command_response(req.request_id, returncode=1),
        # RemoveWorktree (blocking cleanup)
        lambda req: _remove_wt_response(req.request_id),
    ]

    channel = LambdaMockChannel(responses)
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        with patch("orchestrator.failure_classifier.classify_qa_failure", return_value="code"):
            result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.success is False
    assert result.failure_reason is not None
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED


@pytest.mark.asyncio
async def test_impl_pipeline_trace_contains_section_markers() -> None:
    """Trace file contains expected section headers in correct order."""
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

    assert result.execution_id is not None
    trace = await store.get_trace(result.execution_id)
    assert trace is not None

    content = trace.content
    assert "=== IMPLEMENTATION ===" in content
    assert "=== QA RUN 1 ===" in content
    assert "=== CLAUDE REVIEW ===" in content

    # Sections appear in correct order
    impl_pos = content.index("=== IMPLEMENTATION ===")
    qa_pos = content.index("=== QA RUN 1 ===")
    review_pos = content.index("=== CLAUDE REVIEW ===")
    assert impl_pos < qa_pos < review_pos


@pytest.mark.asyncio
async def test_impl_pipeline_trace_contains_fix_attempt_section() -> None:
    """When a QA fix attempt occurs, trace contains QA FIX ATTEMPT section."""
    store = InMemoryStore()
    task, project, spec = await _seed_task_with_spec(store)

    run_command_call = 0

    def _failing_then_passing(req):
        nonlocal run_command_call
        run_command_call += 1
        if run_command_call == 1:
            return RunCommandResponse(
                type="run_command_response",
                request_id=req.request_id,
                success=True,
                returncode=1,
                stdout="test failed",
                stderr="",
            )
        return _run_command_response(req.request_id)

    responses: list = [
        lambda req: _status_response(req.request_id),
        lambda req: _create_wt_response(req.request_id),
        lambda req: _setup_env_response(req.request_id),
        lambda req: _run_claude_response(req.request_id),
        lambda req: _read_file_response(req.request_id),
        _failing_then_passing,   # qa_run 1: fail
        lambda req: _run_claude_response(req.request_id, stdout="fix"),  # fix attempt
        _failing_then_passing,   # qa_run 2: test pass
        _failing_then_passing,   # qa_run 2: lint pass
        lambda req: _get_diff_response(req.request_id),
        lambda req: _review_claude_response(req.request_id),
        lambda req: _remove_wt_response(req.request_id),
    ]

    channel = LambdaMockChannel(responses)
    sequencer = PipelineSequencer(store)

    with (
        patch("orchestrator.sequencer.read_intent", return_value="intent"),
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch.object(PipelineSequencer, "_apply_patch_to_local"),
    ):
        with patch("orchestrator.failure_classifier.classify_qa_failure", return_value="code"):
            result = await sequencer.run_impl_pipeline(channel, task, project, spec)

    assert result.execution_id is not None
    trace = await store.get_trace(result.execution_id)
    assert trace is not None
    content = trace.content
    assert "=== QA FIX ATTEMPT 1 ===" in content
    assert "=== QA RUN 2 ===" in content


@pytest.mark.asyncio
async def test_impl_pipeline_only_one_execution_record_created() -> None:
    """Only one EXECUTION_STARTED event is written — no qa/<uuid> executions."""
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
    started_events = [e for e in exec_events if e.event_type == ev.EXECUTION_STARTED]
    assert len(started_events) == 1

    # Branch name must start with "execution/" not "qa/"
    branch = started_events[0].payload.get("branch_name", "")
    assert branch.startswith("execution/")
