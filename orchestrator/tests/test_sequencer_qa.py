"""Tests for PipelineSequencer.run_qa_pipeline()."""
from __future__ import annotations

import textwrap
from unittest.mock import patch
from uuid import uuid4

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

_RATCHET_YAML = textwrap.dedent("""\
qa:
  steps:
    test: pytest
    lint: ruff check .
  max_fix_attempts: 2
""")


class MockChannel:
    """Mock WorkerChannel with lambda-based responses."""

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


async def _seed_qa_task(store: InMemoryStore) -> tuple[Task, Project, Spec]:
    """Register project, create task + spec, transition to ready_for_qa with execution branch."""
    with patch("core.project_manager.validate_repo"):
        project = await ProjectManager(store).register_project(
            name="test-project",
            repo_url="http://fake",
            local_path="/fake/path",
            config_source="db",
        )

    task = await TaskManager(store).create_task(project.id, "QA task")
    spec = await SpecManager(store).create_spec(task.id, "spec content")
    await SpecManager(store).assign_spec(task.id, spec.id)

    sm = TaskStateMachine(store)
    await sm.transition(task.id, ev.SPEC_QA)
    await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0})

    # Record a fake execution branch
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

    task = await TaskManager(store).get_task(task.id)
    assert task is not None
    return task, project, spec


def _make_handler(*, pass_qa: bool = True, ratchet_yaml: str = _RATCHET_YAML):
    """Create a request handler for happy/fail path."""
    call_counts: dict[str, int] = {}

    def handler(req):
        t = req.type
        call_counts[t] = call_counts.get(t, 0) + 1

        if t == "get_project_status":
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=req.request_id,
                success=True,
                exists=True,
                head_commit="abc123",
            )
        if t == "create_worktree":
            return CreateWorktreeResponse(
                type="create_worktree_response",
                request_id=req.request_id,
                success=True,
                worktree_path="/remote/qa-wt",
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
                content=ratchet_yaml,
            )
        if t == "run_command":
            rc = 0 if pass_qa else 1
            return RunCommandResponse(
                type="run_command_response",
                request_id=req.request_id,
                success=True,
                returncode=rc,
                stdout="ok" if pass_qa else "FAILED",
                stderr="",
            )
        if t == "get_diff":
            return GetDiffResponse(
                type="get_diff_response",
                request_id=req.request_id,
                success=True,
                patch="diff...",
            )
        if t == "run_claude":
            return RunClaudeResponse(
                type="run_claude_response",
                request_id=req.request_id,
                success=True,
                status="completed",
                stdout="QA_PASSED: all checks pass",
                returncode=0,
            )
        if t == "remove_worktree":
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=req.request_id,
                success=True,
            )
        raise AssertionError(f"Unexpected request type: {t!r}")

    return handler


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_pipeline_happy_path_transitions_to_ready_for_deployment() -> None:
    from unittest.mock import MagicMock
    store = InMemoryStore()
    task, project, spec = await _seed_qa_task(store)

    channel = MockChannel(_make_handler(pass_qa=True))
    sequencer = PipelineSequencer(store)

    fake_diff = MagicMock(returncode=0, stdout="diff --git a/f.txt b/f.txt\n+new\n")
    with (
        patch("orchestrator.sequencer._get_local_head", return_value="abc123"),
        patch("orchestrator.sequencer.subprocess.run", return_value=fake_diff),
    ):
        result = await sequencer.run_qa_pipeline(channel, task, project, spec)

    assert result.success is True
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.READY_FOR_DEPLOYMENT


@pytest.mark.asyncio
async def test_qa_pipeline_no_ratchet_yaml_skips_to_ready_for_deployment() -> None:
    store = InMemoryStore()
    task, project, spec = await _seed_qa_task(store)

    def handler(req):
        if req.type == "get_project_status":
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=req.request_id,
                success=True,
                exists=True,
                head_commit="abc123",
            )
        if req.type == "create_worktree":
            return CreateWorktreeResponse(
                type="create_worktree_response",
                request_id=req.request_id,
                success=True,
                worktree_path="/remote/wt",
            )
        if req.type == "setup_environment":
            return SetupEnvironmentResponse(
                type="setup_environment_response",
                request_id=req.request_id,
                success=True,
            )
        if req.type == "read_file":
            return ReadFileResponse(
                type="read_file_response",
                request_id=req.request_id,
                success=True,
                content=None,  # no ratchet.yaml
            )
        if req.type == "remove_worktree":
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=req.request_id,
                success=True,
            )
        raise AssertionError(f"Unexpected: {req.type!r}")

    channel = MockChannel(handler)
    sequencer = PipelineSequencer(store)

    with patch("orchestrator.sequencer._get_local_head", return_value="abc123"):
        result = await sequencer.run_qa_pipeline(channel, task, project, spec)

    assert result.success is True
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.READY_FOR_DEPLOYMENT


@pytest.mark.asyncio
async def test_qa_pipeline_fails_with_fix_attempt_requeues_for_qa() -> None:
    store = InMemoryStore()
    task, project, spec = await _seed_qa_task(store)

    channel = MockChannel(_make_handler(pass_qa=False))
    sequencer = PipelineSequencer(store)

    with patch("orchestrator.sequencer._get_local_head", return_value="abc123"):
        result = await sequencer.run_qa_pipeline(channel, task, project, spec)

    # qa_fix_attempts=0 < max=2, so should re-queue for QA with attempts incremented
    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.READY_FOR_QA

    # Verify RunClaude fix attempt was sent
    sent_types = [r.type for r in channel.sent_requests]
    assert "run_claude" in sent_types


@pytest.mark.asyncio
async def test_qa_pipeline_max_retries_exhausted_transitions_to_blocked() -> None:
    store = InMemoryStore()
    task, project, spec = await _seed_qa_task(store)

    # Manually set qa_fix_attempts to max (2)
    await store.append_event(
        aggregate_id=task.id,
        aggregate_type="task",
        event_type=ev.TASK_STATUS_CHANGED,
        payload={
            "from_status": ev.READY_FOR_QA,
            "to_status": ev.READY_FOR_QA,
            "status": ev.READY_FOR_QA,
            "qa_fix_attempts": 2,
        },
    )

    channel = MockChannel(_make_handler(pass_qa=False))
    sequencer = PipelineSequencer(store)

    with patch("orchestrator.sequencer._get_local_head", return_value="abc123"):
        result = await sequencer.run_qa_pipeline(channel, task, project, spec)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED

    # RunClaude should NOT be sent when max retries exhausted
    sent_types = [r.type for r in channel.sent_requests]
    assert "run_claude" not in sent_types


@pytest.mark.asyncio
async def test_qa_pipeline_abort_transitions_to_blocked() -> None:
    store = InMemoryStore()
    task, project, spec = await _seed_qa_task(store)

    def handler(req):
        if req.type == "get_project_status":
            return GetProjectStatusResponse(
                type="get_project_status_response",
                request_id=req.request_id,
                success=True,
                exists=True,
                head_commit="abc123",
            )
        if req.type == "create_worktree":
            raise PipelineAbort("create_worktree", "disk full", req.request_id)
        if req.type == "remove_worktree":
            return RemoveWorktreeResponse(
                type="remove_worktree_response",
                request_id=req.request_id,
                success=True,
            )
        raise AssertionError(f"Unexpected: {req.type!r}")

    channel = MockChannel(handler)
    sequencer = PipelineSequencer(store)

    with patch("orchestrator.sequencer._get_local_head", return_value="abc123"):
        result = await sequencer.run_qa_pipeline(channel, task, project, spec)

    assert result.success is False
    status = await TaskStateMachine(store).get_current_status(task.id)
    assert status == ev.BLOCKED
