"""
Pydantic message schemas for the orchestrator↔remote worker WebSocket protocol.
Messages are JSON-encoded with a top-level `type` discriminator field.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# ---------------------------------------------------------------------------
# Worker → Orchestrator messages
# ---------------------------------------------------------------------------


class WorkerHelloMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["worker_hello"]
    worker_id: str
    version: str
    capabilities: list[str]


class HeartbeatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["heartbeat"]
    worker_id: str
    timestamp_utc: str
    current_task_id: str | None


class ExecutionStartedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["execution_started"]
    worker_id: str
    task_id: str
    execution_id: str
    branch_name: str | None
    timestamp_utc: str


class ExecutionCompletedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["execution_completed"]
    worker_id: str
    task_id: str
    execution_id: str
    patch: str
    timestamp_utc: str


class ExecutionFailedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["execution_failed"]
    worker_id: str
    task_id: str
    execution_id: str
    failure_reason: str
    timestamp_utc: str


class QuestionAskedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["question_asked"]
    worker_id: str
    task_id: str
    execution_id: str
    question_index: int
    question: str


class LogLineMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["log_line"]
    worker_id: str
    task_id: str | None
    execution_id: str | None
    level: str
    message: str
    timestamp_utc: str


# ---------------------------------------------------------------------------
# Orchestrator → Worker messages
# ---------------------------------------------------------------------------


class AssignTaskMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["assign_task"]
    task_id: str
    spec_id: str
    spec_content: str
    project_id: str
    project_name: str
    project_local_path: str
    project_intent_md: str | None
    project_ratchet_yaml: str | None
    project_config_source: str
    git_bundle_b64: str


class ProvideAnswerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["provide_answer"]
    task_id: str
    question_index: int
    answer: str
    answered_by: str


class CancelTaskMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cancel_task"]
    task_id: str
    reason: str


class OrchestratorAckMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["orchestrator_ack"]
    worker_id: str
    accepted: bool
    message: str | None


# ---------------------------------------------------------------------------
# Discriminated unions
# ---------------------------------------------------------------------------

AnyWorkerMessage = Annotated[
    WorkerHelloMessage
    | HeartbeatMessage
    | ExecutionStartedMessage
    | ExecutionCompletedMessage
    | ExecutionFailedMessage
    | QuestionAskedMessage
    | LogLineMessage,
    Field(discriminator="type"),
]

AnyOrchestratorMessage = Annotated[
    AssignTaskMessage
    | ProvideAnswerMessage
    | CancelTaskMessage
    | OrchestratorAckMessage,
    Field(discriminator="type"),
]

_worker_adapter: TypeAdapter[AnyWorkerMessage] = TypeAdapter(AnyWorkerMessage)
_orchestrator_adapter: TypeAdapter[AnyOrchestratorMessage] = TypeAdapter(
    AnyOrchestratorMessage
)


# ---------------------------------------------------------------------------
# Parse functions
# ---------------------------------------------------------------------------


def parse_worker_message(raw: str) -> AnyWorkerMessage:
    """Parse a JSON string into a worker message. Raises ValidationError on failure."""
    return _worker_adapter.validate_json(raw)


def parse_orchestrator_message(raw: str) -> AnyOrchestratorMessage:
    """Parse a JSON string into an orchestrator message. Raises ValidationError on failure."""
    return _orchestrator_adapter.validate_json(raw)
