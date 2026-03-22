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
    projects: dict[str, str] = {}


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
# Command request models (Orchestrator → Worker)
# ---------------------------------------------------------------------------


class GetProjectStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["get_project_status"]
    request_id: str
    project_id: str


class SetupProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["setup_project"]
    request_id: str
    project_id: str
    bundle_b64: str
    path: str


class UpdateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["update_project"]
    request_id: str
    project_id: str
    patch_b64: str


class CreateWorktreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["create_worktree"]
    request_id: str
    project_id: str
    execution_id: str
    base_commit: str


class RemoveWorktreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["remove_worktree"]
    request_id: str
    project_id: str
    execution_id: str


class GetDiffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["get_diff"]
    request_id: str
    project_id: str
    execution_id: str


class RunClaudeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["run_claude"]
    request_id: str
    execution_id: str
    prompt: str
    model: str
    tools: list[str]
    cwd: str


class RunCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["run_command"]
    request_id: str
    execution_id: str
    cmd: list[str]
    cwd: str


class ReadFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["read_file"]
    request_id: str
    project_id: str
    execution_id: str
    path: str


class SetupEnvironmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["setup_environment"]
    request_id: str
    project_id: str
    execution_id: str
    symlinks: list[str]  # relative paths from project root to symlink into worktree


class GetStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["get_status"]
    request_id: str


# ---------------------------------------------------------------------------
# Command response models (Worker → Orchestrator)
# ---------------------------------------------------------------------------


class GetProjectStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["get_project_status_response"]
    request_id: str
    success: bool
    error: str | None = None
    exists: bool
    path: str | None = None
    head_commit: str | None = None


class SetupProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["setup_project_response"]
    request_id: str
    success: bool
    error: str | None = None


class UpdateProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["update_project_response"]
    request_id: str
    success: bool
    error: str | None = None


class CreateWorktreeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["create_worktree_response"]
    request_id: str
    success: bool
    error: str | None = None
    worktree_path: str | None = None


class RemoveWorktreeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["remove_worktree_response"]
    request_id: str
    success: bool
    error: str | None = None


class GetDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["get_diff_response"]
    request_id: str
    success: bool
    error: str | None = None
    patch: str | None = None


class RunClaudeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["run_claude_response"]
    request_id: str
    success: bool
    error: str | None = None
    status: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    returncode: int | None = None


class RunCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["run_command_response"]
    request_id: str
    success: bool
    error: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    returncode: int | None = None


class ReadFileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["read_file_response"]
    request_id: str
    success: bool
    error: str | None = None
    content: str | None = None


class SetupEnvironmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["setup_environment_response"]
    request_id: str
    success: bool
    error: str | None = None


class GetStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["get_status_response"]
    request_id: str
    success: bool
    error: str | None = None
    current_execution_id: str | None = None


# ---------------------------------------------------------------------------
# Discriminated unions
# ---------------------------------------------------------------------------

AnyCommandRequest = Annotated[
    GetProjectStatusRequest
    | SetupProjectRequest
    | UpdateProjectRequest
    | CreateWorktreeRequest
    | RemoveWorktreeRequest
    | GetDiffRequest
    | RunClaudeRequest
    | RunCommandRequest
    | ReadFileRequest
    | SetupEnvironmentRequest
    | GetStatusRequest,
    Field(discriminator="type"),
]

AnyCommandResponse = Annotated[
    GetProjectStatusResponse
    | SetupProjectResponse
    | UpdateProjectResponse
    | CreateWorktreeResponse
    | RemoveWorktreeResponse
    | GetDiffResponse
    | RunClaudeResponse
    | RunCommandResponse
    | ReadFileResponse
    | SetupEnvironmentResponse
    | GetStatusResponse,
    Field(discriminator="type"),
]

AnyWorkerMessage = Annotated[
    WorkerHelloMessage
    | HeartbeatMessage
    | ExecutionStartedMessage
    | ExecutionCompletedMessage
    | ExecutionFailedMessage
    | QuestionAskedMessage
    | LogLineMessage
    | GetProjectStatusResponse
    | SetupProjectResponse
    | UpdateProjectResponse
    | CreateWorktreeResponse
    | RemoveWorktreeResponse
    | GetDiffResponse
    | RunClaudeResponse
    | RunCommandResponse
    | ReadFileResponse
    | SetupEnvironmentResponse
    | GetStatusResponse,
    Field(discriminator="type"),
]

AnyOrchestratorMessage = Annotated[
    AssignTaskMessage
    | ProvideAnswerMessage
    | CancelTaskMessage
    | OrchestratorAckMessage
    | GetProjectStatusRequest
    | SetupProjectRequest
    | UpdateProjectRequest
    | CreateWorktreeRequest
    | RemoveWorktreeRequest
    | GetDiffRequest
    | RunClaudeRequest
    | RunCommandRequest
    | ReadFileRequest
    | SetupEnvironmentRequest
    | GetStatusRequest,
    Field(discriminator="type"),
]

_worker_adapter: TypeAdapter[AnyWorkerMessage] = TypeAdapter(AnyWorkerMessage)
_orchestrator_adapter: TypeAdapter[AnyOrchestratorMessage] = TypeAdapter(
    AnyOrchestratorMessage
)
_command_request_adapter: TypeAdapter[AnyCommandRequest] = TypeAdapter(AnyCommandRequest)
_command_response_adapter: TypeAdapter[AnyCommandResponse] = TypeAdapter(
    AnyCommandResponse
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


def parse_command_request(raw: str) -> AnyCommandRequest:
    """Parse a JSON string into a command request. Raises ValidationError on failure."""
    return _command_request_adapter.validate_json(raw)


def parse_command_response(raw: str) -> AnyCommandResponse:
    """Parse a JSON string into a command response. Raises ValidationError on failure."""
    return _command_response_adapter.validate_json(raw)
