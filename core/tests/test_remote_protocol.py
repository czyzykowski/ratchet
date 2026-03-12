"""Round-trip serialization tests for the WebSocket message protocol."""

import pytest
from pydantic import ValidationError

from core.remote_protocol import (
    AssignTaskMessage,
    CancelTaskMessage,
    ExecutionCompletedMessage,
    ExecutionFailedMessage,
    ExecutionStartedMessage,
    HeartbeatMessage,
    LogLineMessage,
    OrchestratorAckMessage,
    ProvideAnswerMessage,
    QuestionAskedMessage,
    WorkerHelloMessage,
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
    )
    parsed = parse_worker_message(msg.model_dump_json())
    assert isinstance(parsed, WorkerHelloMessage)
    assert parsed.worker_id == "host-123"
    assert parsed.version == "1.0.0"
    assert parsed.capabilities == ["claude_code"]


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
