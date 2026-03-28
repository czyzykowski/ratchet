"""Round-trip serialization tests for the WebSocket message protocol."""

import pytest
from pydantic import ValidationError

from core.remote_protocol import (
    AssignTaskMessage,
    CancelTaskMessage,
    CreateWorktreeRequest,
    CreateWorktreeResponse,
    ExecutionCompletedMessage,
    ExecutionFailedMessage,
    ExecutionStartedMessage,
    GetDiffRequest,
    GetDiffResponse,
    GetProjectStatusRequest,
    GetProjectStatusResponse,
    GetStatusRequest,
    GetStatusResponse,
    HeartbeatMessage,
    LogLineMessage,
    OrchestratorAckMessage,
    ProvideAnswerMessage,
    QuestionAskedMessage,
    ReadFileRequest,
    ReadFileResponse,
    RemoveWorktreeRequest,
    RemoveWorktreeResponse,
    RunClaudeRequest,
    RunClaudeResponse,
    RunCommandRequest,
    RunCommandResponse,
    SetupEnvironmentRequest,
    SetupEnvironmentResponse,
    SetupProjectRequest,
    SetupProjectResponse,
    UpdateProjectRequest,
    UpdateProjectResponse,
    WorkerHelloMessage,
    parse_command_request,
    parse_command_response,
    parse_orchestrator_message,
    parse_worker_message,
)

# ---------------------------------------------------------------------------
# Worker → Orchestrator round-trip tests
# ---------------------------------------------------------------------------


def test_worker_hello_round_trip():
    msg = WorkerHelloMessage(
        type="worker_hello",
        worker_id="host-123",
        version="1.0.0",
        capabilities=["claude_code"],
        projects={"proj-1": "/home/user/proj-1"},
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, WorkerHelloMessage)
    assert parsed.worker_id == "host-123"
    assert parsed.version == "1.0.0"
    assert parsed.capabilities == ["claude_code"]
    assert parsed.projects == {"proj-1": "/home/user/proj-1"}


def test_heartbeat_round_trip():
    msg = HeartbeatMessage(
        type="heartbeat",
        worker_id="host-123",
        timestamp_utc="2026-03-12T00:00:00Z",
        current_task_id="task-uuid",
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, HeartbeatMessage)
    assert parsed.current_task_id == "task-uuid"


def test_heartbeat_none_task_round_trip():
    msg = HeartbeatMessage(
        type="heartbeat",
        worker_id="host-123",
        timestamp_utc="2026-03-12T00:00:00Z",
        current_task_id=None,
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, HeartbeatMessage)
    assert parsed.current_task_id is None


def test_execution_started_round_trip():
    msg = ExecutionStartedMessage(
        type="execution_started",
        worker_id="host-123",
        task_id="task-uuid",
        execution_id="exec-uuid",
        branch_name="feat/something",
        timestamp_utc="2026-03-12T00:00:00Z",
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, ExecutionStartedMessage)
    assert parsed.branch_name == "feat/something"


def test_execution_completed_round_trip():
    msg = ExecutionCompletedMessage(
        type="execution_completed",
        worker_id="host-123",
        task_id="task-uuid",
        execution_id="exec-uuid",
        patch="--- a\n+++ b\n",
        timestamp_utc="2026-03-12T00:00:00Z",
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, ExecutionCompletedMessage)
    assert parsed.execution_id == "exec-uuid"


def test_execution_failed_round_trip():
    msg = ExecutionFailedMessage(
        type="execution_failed",
        worker_id="host-123",
        task_id="task-uuid",
        execution_id="exec-uuid",
        failure_reason="subprocess exited with code 1",
        timestamp_utc="2026-03-12T00:00:00Z",
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, ExecutionFailedMessage)
    assert parsed.failure_reason == "subprocess exited with code 1"


def test_question_asked_round_trip():
    msg = QuestionAskedMessage(
        type="question_asked",
        worker_id="host-123",
        task_id="task-uuid",
        execution_id="exec-uuid",
        question_index=0,
        question="What should I do?",
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, QuestionAskedMessage)
    assert parsed.question_index == 0
    assert parsed.question == "What should I do?"


def test_log_line_round_trip():
    msg = LogLineMessage(
        type="log_line",
        worker_id="host-123",
        task_id="task-uuid",
        execution_id="exec-uuid",
        level="info",
        message="Starting task execution",
        timestamp_utc="2026-03-12T00:00:00Z",
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, LogLineMessage)
    assert parsed.level == "info"


def test_log_line_none_task_round_trip():
    msg = LogLineMessage(
        type="log_line",
        worker_id="host-123",
        task_id=None,
        execution_id=None,
        level="warning",
        message="No task running",
        timestamp_utc="2026-03-12T00:00:00Z",
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, LogLineMessage)
    assert parsed.task_id is None
    assert parsed.execution_id is None


# ---------------------------------------------------------------------------
# Orchestrator → Worker round-trip tests
# ---------------------------------------------------------------------------


def test_assign_task_round_trip():
    msg = AssignTaskMessage(
        type="assign_task",
        task_id="task-uuid",
        spec_id="spec-uuid",
        spec_content="## Spec\nDo something.",
        project_id="proj-uuid",
        project_name="my-project",
        project_local_path="/home/user/project",
        project_intent_md="# Intent\nBuild stuff.",
        project_ratchet_yaml="qa:\n  test: pytest",
        project_config_source="disk",
        git_bundle_b64="YnVuZGxlZGF0YQ==",
    )
    parsed = parse_orchestrator_message(msg.model_dump_json())
    assert isinstance(parsed, AssignTaskMessage)
    assert parsed.project_config_source == "disk"
    assert parsed.project_intent_md == "# Intent\nBuild stuff."


def test_assign_task_none_optional_fields_round_trip():
    msg = AssignTaskMessage(
        type="assign_task",
        task_id="task-uuid",
        spec_id="spec-uuid",
        spec_content="## Spec",
        project_id="proj-uuid",
        project_name="my-project",
        project_local_path="/home/user/project",
        project_intent_md=None,
        project_ratchet_yaml=None,
        project_config_source="db",
        git_bundle_b64="YnVuZGxlZGF0YQ==",
    )
    parsed = parse_orchestrator_message(msg.model_dump_json())
    assert isinstance(parsed, AssignTaskMessage)
    assert parsed.project_intent_md is None
    assert parsed.project_ratchet_yaml is None


def test_provide_answer_round_trip():
    msg = ProvideAnswerMessage(
        type="provide_answer",
        task_id="task-uuid",
        question_index=2,
        answer="Yes, do that.",
        answered_by="cli",
    )
    parsed = parse_orchestrator_message(msg.model_dump_json())
    assert isinstance(parsed, ProvideAnswerMessage)
    assert parsed.question_index == 2
    assert parsed.answered_by == "cli"


def test_cancel_task_round_trip():
    msg = CancelTaskMessage(
        type="cancel_task",
        task_id="task-uuid",
        reason="User requested cancellation",
    )
    parsed = parse_orchestrator_message(msg.model_dump_json())
    assert isinstance(parsed, CancelTaskMessage)
    assert parsed.reason == "User requested cancellation"


def test_orchestrator_ack_round_trip():
    msg = OrchestratorAckMessage(
        type="orchestrator_ack",
        worker_id="host-123",
        accepted=True,
        message=None,
    )
    parsed = parse_orchestrator_message(msg.model_dump_json())
    assert isinstance(parsed, OrchestratorAckMessage)
    assert parsed.accepted is True
    assert parsed.message is None


def test_orchestrator_ack_rejection_round_trip():
    msg = OrchestratorAckMessage(
        type="orchestrator_ack",
        worker_id="host-123",
        accepted=False,
        message="Worker version too old",
    )
    parsed = parse_orchestrator_message(msg.model_dump_json())
    assert isinstance(parsed, OrchestratorAckMessage)
    assert parsed.accepted is False
    assert parsed.message == "Worker version too old"


# ---------------------------------------------------------------------------
# Validation error tests
# ---------------------------------------------------------------------------


def test_unknown_worker_type_raises():
    with pytest.raises(ValidationError):
        parse_worker_message('{"type": "unknown_type", "worker_id": "x"}')


def test_unknown_orchestrator_type_raises():
    with pytest.raises(ValidationError):
        parse_orchestrator_message('{"type": "unknown_type", "task_id": "x"}')


def test_extra_field_on_worker_message_raises():
    with pytest.raises(ValidationError):
        parse_worker_message(
            '{"type": "heartbeat", "worker_id": "x", "timestamp_utc": "2026-03-12T00:00:00Z", '
            '"current_task_id": null, "extra_field": "foo"}'
        )


def test_extra_field_on_orchestrator_message_raises():
    with pytest.raises(ValidationError):
        parse_orchestrator_message(
            '{"type": "cancel_task", "task_id": "x", "reason": "y", "extra_field": "foo"}'
        )


def test_worker_parser_rejects_orchestrator_message_type():
    with pytest.raises(ValidationError):
        parse_worker_message(
            '{"type": "assign_task", "task_id": "x", "spec_id": "s", '
            '"spec_content": "c", "project_id": "p", "project_name": "n", '
            '"project_local_path": "/p", "project_intent_md": null, '
            '"project_ratchet_yaml": null, "project_config_source": "disk"}'
        )


def test_orchestrator_parser_rejects_worker_message_type():
    with pytest.raises(ValidationError):
        parse_orchestrator_message(
            '{"type": "heartbeat", "worker_id": "x", "timestamp_utc": "2026-03-12T00:00:00Z", '
            '"current_task_id": null}'
        )


# ---------------------------------------------------------------------------
# WorkerHelloMessage backward compatibility
# ---------------------------------------------------------------------------


def test_worker_hello_backward_compat_without_projects():
    """WorkerHelloMessage without projects field parses with default empty dict."""
    raw = '{"type": "worker_hello", "worker_id": "host-1", "version": "1.0.0", "capabilities": []}'
    parsed = parse_worker_message(raw)
    assert isinstance(parsed, WorkerHelloMessage)
    assert parsed.projects == {}


# ---------------------------------------------------------------------------
# Command request round-trip tests
# ---------------------------------------------------------------------------


def test_get_project_status_request_round_trip():
    msg = GetProjectStatusRequest(
        type="get_project_status",
        request_id="req-1",
        project_id="proj-uuid",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, GetProjectStatusRequest)
    assert parsed.request_id == "req-1"
    assert parsed.project_id == "proj-uuid"


def test_setup_project_request_round_trip():
    msg = SetupProjectRequest(
        type="setup_project",
        request_id="req-2",
        project_id="proj-uuid",
        bundle_b64="YnVuZGxl",
        path="/home/worker/proj",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, SetupProjectRequest)
    assert parsed.bundle_b64 == "YnVuZGxl"
    assert parsed.path == "/home/worker/proj"


def test_update_project_request_round_trip():
    msg = UpdateProjectRequest(
        type="update_project",
        request_id="req-3",
        project_id="proj-uuid",
        patch_b64="cGF0Y2g=",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, UpdateProjectRequest)
    assert parsed.patch_b64 == "cGF0Y2g="


def test_create_worktree_request_round_trip():
    msg = CreateWorktreeRequest(
        type="create_worktree",
        request_id="req-4",
        project_id="proj-uuid",
        execution_id="exec-uuid",
        base_commit="abc123",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, CreateWorktreeRequest)
    assert parsed.base_commit == "abc123"


def test_remove_worktree_request_round_trip():
    msg = RemoveWorktreeRequest(
        type="remove_worktree",
        request_id="req-5",
        project_id="proj-uuid",
        execution_id="exec-uuid",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, RemoveWorktreeRequest)
    assert parsed.execution_id == "exec-uuid"


def test_get_diff_request_round_trip():
    msg = GetDiffRequest(
        type="get_diff",
        request_id="req-6",
        project_id="proj-uuid",
        execution_id="exec-uuid",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, GetDiffRequest)
    assert parsed.request_id == "req-6"


def test_run_claude_request_round_trip():
    msg = RunClaudeRequest(
        type="run_claude",
        request_id="req-7",
        execution_id="exec-uuid",
        prompt="Do the thing",
        model="claude-sonnet-4-6",
        tools=["read", "write"],
        cwd="/home/worker/proj",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, RunClaudeRequest)
    assert parsed.tools == ["read", "write"]
    assert parsed.model == "claude-sonnet-4-6"


def test_run_command_request_round_trip():
    msg = RunCommandRequest(
        type="run_command",
        request_id="req-8",
        execution_id="exec-uuid",
        cmd=["pytest", "-v"],
        cwd="/home/worker/proj",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, RunCommandRequest)
    assert parsed.cmd == ["pytest", "-v"]


def test_read_file_request_round_trip():
    msg = ReadFileRequest(
        type="read_file",
        request_id="req-9",
        project_id="proj-uuid",
        execution_id="exec-uuid",
        path="src/main.py",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, ReadFileRequest)
    assert parsed.path == "src/main.py"


def test_setup_environment_request_round_trip():
    msg = SetupEnvironmentRequest(
        type="setup_environment",
        request_id="req-10",
        project_id="proj-uuid",
        execution_id="exec-uuid",
        symlinks=[".venv", "node_modules"],
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, SetupEnvironmentRequest)
    assert parsed.symlinks == [".venv", "node_modules"]


def test_get_status_request_round_trip():
    msg = GetStatusRequest(
        type="get_status",
        request_id="req-11",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, GetStatusRequest)
    assert parsed.request_id == "req-11"


# ---------------------------------------------------------------------------
# Command response round-trip tests (success and error cases)
# ---------------------------------------------------------------------------


def test_get_project_status_response_success_round_trip():
    msg = GetProjectStatusResponse(
        type="get_project_status_response",
        request_id="req-1",
        success=True,
        exists=True,
        path="/home/worker/proj",
        head_commit="abc123",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetProjectStatusResponse)
    assert parsed.exists is True
    assert parsed.head_commit == "abc123"


def test_get_project_status_response_error_round_trip():
    msg = GetProjectStatusResponse(
        type="get_project_status_response",
        request_id="req-1",
        success=False,
        error="Project not found",
        exists=False,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetProjectStatusResponse)
    assert parsed.success is False
    assert parsed.error == "Project not found"
    assert parsed.exists is False


def test_setup_project_response_success_round_trip():
    msg = SetupProjectResponse(
        type="setup_project_response",
        request_id="req-2",
        success=True,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, SetupProjectResponse)
    assert parsed.success is True
    assert parsed.error is None


def test_setup_project_response_error_round_trip():
    msg = SetupProjectResponse(
        type="setup_project_response",
        request_id="req-2",
        success=False,
        error="Disk full",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, SetupProjectResponse)
    assert parsed.error == "Disk full"


def test_update_project_response_success_round_trip():
    msg = UpdateProjectResponse(
        type="update_project_response",
        request_id="req-3",
        success=True,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, UpdateProjectResponse)
    assert parsed.success is True


def test_update_project_response_error_round_trip():
    msg = UpdateProjectResponse(
        type="update_project_response",
        request_id="req-3",
        success=False,
        error="Patch conflict",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, UpdateProjectResponse)
    assert parsed.error == "Patch conflict"


def test_create_worktree_response_success_round_trip():
    msg = CreateWorktreeResponse(
        type="create_worktree_response",
        request_id="req-4",
        success=True,
        worktree_path="/home/worker/worktrees/exec-uuid",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, CreateWorktreeResponse)
    assert parsed.worktree_path == "/home/worker/worktrees/exec-uuid"


def test_create_worktree_response_error_round_trip():
    msg = CreateWorktreeResponse(
        type="create_worktree_response",
        request_id="req-4",
        success=False,
        error="Base commit not found",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, CreateWorktreeResponse)
    assert parsed.success is False
    assert parsed.worktree_path is None


def test_remove_worktree_response_success_round_trip():
    msg = RemoveWorktreeResponse(
        type="remove_worktree_response",
        request_id="req-5",
        success=True,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, RemoveWorktreeResponse)
    assert parsed.success is True


def test_remove_worktree_response_error_round_trip():
    msg = RemoveWorktreeResponse(
        type="remove_worktree_response",
        request_id="req-5",
        success=False,
        error="Worktree not found",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, RemoveWorktreeResponse)
    assert parsed.error == "Worktree not found"


def test_get_diff_response_success_round_trip():
    msg = GetDiffResponse(
        type="get_diff_response",
        request_id="req-6",
        success=True,
        patch="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetDiffResponse)
    assert parsed.patch is not None
    assert parsed.patch.startswith("---")


def test_get_diff_response_error_round_trip():
    msg = GetDiffResponse(
        type="get_diff_response",
        request_id="req-6",
        success=False,
        error="Execution not found",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetDiffResponse)
    assert parsed.patch is None


def test_run_claude_response_success_round_trip():
    msg = RunClaudeResponse(
        type="run_claude_response",
        request_id="req-7",
        success=True,
        status="COMPLETED",
        stdout="Done",
        stderr="",
        returncode=0,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, RunClaudeResponse)
    assert parsed.status == "COMPLETED"
    assert parsed.returncode == 0


def test_run_claude_response_error_round_trip():
    msg = RunClaudeResponse(
        type="run_claude_response",
        request_id="req-7",
        success=False,
        error="Claude subprocess crashed",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, RunClaudeResponse)
    assert parsed.success is False
    assert parsed.status is None


def test_run_command_response_success_round_trip():
    msg = RunCommandResponse(
        type="run_command_response",
        request_id="req-8",
        success=True,
        stdout="all tests passed",
        stderr="",
        returncode=0,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, RunCommandResponse)
    assert parsed.stdout == "all tests passed"
    assert parsed.returncode == 0


def test_run_command_response_error_round_trip():
    msg = RunCommandResponse(
        type="run_command_response",
        request_id="req-8",
        success=False,
        error="Command not found",
        returncode=127,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, RunCommandResponse)
    assert parsed.returncode == 127


def test_read_file_response_success_round_trip():
    msg = ReadFileResponse(
        type="read_file_response",
        request_id="req-9",
        success=True,
        content="file contents here",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, ReadFileResponse)
    assert parsed.content == "file contents here"


def test_read_file_response_error_round_trip():
    msg = ReadFileResponse(
        type="read_file_response",
        request_id="req-9",
        success=False,
        error="File not found",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, ReadFileResponse)
    assert parsed.content is None


def test_setup_environment_response_success_round_trip():
    msg = SetupEnvironmentResponse(
        type="setup_environment_response",
        request_id="req-10",
        success=True,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, SetupEnvironmentResponse)
    assert parsed.success is True


def test_setup_environment_response_error_round_trip():
    msg = SetupEnvironmentResponse(
        type="setup_environment_response",
        request_id="req-10",
        success=False,
        error="Symlink target missing",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, SetupEnvironmentResponse)
    assert parsed.error == "Symlink target missing"


def test_get_status_response_success_round_trip():
    msg = GetStatusResponse(
        type="get_status_response",
        request_id="req-11",
        success=True,
        current_execution_id="exec-uuid",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetStatusResponse)
    assert parsed.current_execution_id == "exec-uuid"


def test_get_status_response_error_round_trip():
    msg = GetStatusResponse(
        type="get_status_response",
        request_id="req-11",
        success=False,
        error="Internal error",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetStatusResponse)
    assert parsed.current_execution_id is None


# ---------------------------------------------------------------------------
# Command request/response also parseable by main parsers
# ---------------------------------------------------------------------------


def test_command_request_parseable_by_orchestrator_parser():
    msg = GetStatusRequest(type="get_status", request_id="req-1")
    parsed = parse_orchestrator_message(msg.model_dump_json())
    assert isinstance(parsed, GetStatusRequest)


def test_command_response_parseable_by_worker_parser():
    msg = GetStatusResponse(
        type="get_status_response", request_id="req-1", success=True
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, GetStatusResponse)


# ---------------------------------------------------------------------------
# Command validation tests
# ---------------------------------------------------------------------------


def test_unknown_command_request_type_raises():
    with pytest.raises(ValidationError):
        parse_command_request('{"type": "unknown_command", "request_id": "x"}')


def test_unknown_command_response_type_raises():
    with pytest.raises(ValidationError):
        parse_command_response('{"type": "unknown_response", "request_id": "x"}')


def test_extra_field_on_command_request_raises():
    with pytest.raises(ValidationError):
        parse_command_request(
            '{"type": "get_status", "request_id": "x", "extra_field": "foo"}'
        )


def test_extra_field_on_command_response_raises():
    with pytest.raises(ValidationError):
        parse_command_response(
            '{"type": "get_status_response", "request_id": "x",'
            ' "success": true, "extra_field": "foo"}'
        )


# ---------------------------------------------------------------------------
# GetSessionProgress request/response tests
# ---------------------------------------------------------------------------


def test_get_session_progress_request_round_trip():
    from core.remote_protocol import GetSessionProgressRequest

    msg = GetSessionProgressRequest(
        type="get_session_progress",
        request_id="req-sp-1",
        execution_id="exec-uuid",
    )
    parsed = parse_command_request(msg.model_dump_json())
    assert isinstance(parsed, GetSessionProgressRequest)
    assert parsed.request_id == "req-sp-1"
    assert parsed.execution_id == "exec-uuid"


def test_get_session_progress_request_parseable_by_orchestrator_parser():
    from core.remote_protocol import GetSessionProgressRequest

    msg = GetSessionProgressRequest(
        type="get_session_progress",
        request_id="req-sp-1",
        execution_id="exec-uuid",
    )
    parsed = parse_orchestrator_message(msg.model_dump_json())
    assert isinstance(parsed, GetSessionProgressRequest)


def test_get_session_progress_response_success_round_trip():
    from core.remote_protocol import GetSessionProgressResponse

    msg = GetSessionProgressResponse(
        type="get_session_progress_response",
        request_id="req-sp-1",
        success=True,
        messages=[{"type": "assistant", "content": "hello"}],
        total_messages=5,
        file_size_bytes=1024,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetSessionProgressResponse)
    assert parsed.success is True
    assert parsed.messages == [{"type": "assistant", "content": "hello"}]
    assert parsed.total_messages == 5
    assert parsed.file_size_bytes == 1024


def test_get_session_progress_response_empty_messages_round_trip():
    from core.remote_protocol import GetSessionProgressResponse

    msg = GetSessionProgressResponse(
        type="get_session_progress_response",
        request_id="req-sp-1",
        success=True,
        messages=[],
        total_messages=0,
        file_size_bytes=0,
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetSessionProgressResponse)
    assert parsed.messages == []


def test_get_session_progress_response_error_round_trip():
    from core.remote_protocol import GetSessionProgressResponse

    msg = GetSessionProgressResponse(
        type="get_session_progress_response",
        request_id="req-sp-1",
        success=False,
        error="JSONL file not found",
    )
    parsed = parse_command_response(msg.model_dump_json())
    assert isinstance(parsed, GetSessionProgressResponse)
    assert parsed.success is False
    assert parsed.error == "JSONL file not found"
    assert parsed.messages is None


def test_get_session_progress_response_parseable_by_worker_parser():
    from core.remote_protocol import GetSessionProgressResponse

    msg = GetSessionProgressResponse(
        type="get_session_progress_response",
        request_id="req-sp-1",
        success=True,
        messages=[],
        total_messages=0,
        file_size_bytes=0,
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, GetSessionProgressResponse)
