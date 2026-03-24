from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class Event(BaseModel):
    id: UUID
    aggregate_id: UUID
    aggregate_type: str
    event_type: str
    payload: dict[str, Any]
    schema_version: int = 1
    occurred_at: datetime
    sequence: int


class Project(BaseModel):
    id: UUID
    name: str
    repo_url: str
    local_path: str
    status: str
    created_at: datetime
    updated_at: datetime
    config_source: str = "disk"
    claude_md: str | None = None
    intent_md: str | None = None
    ratchet_yaml: str | None = None
    required_capabilities: list[str] = []


class Task(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    status: str
    current_spec_id: UUID | None
    refinement_count: int
    created_at: datetime
    updated_at: datetime
    depends_on: list[str] = []
    required_capabilities: list[str] = []
    merge_commit_sha: str | None = None


class Spec(BaseModel):
    id: UUID
    task_id: UUID
    previous_spec_id: UUID | None
    content: str
    created_at: datetime


class Feature(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    description: str
    session_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    abandoned: bool = False


class HighLevelSpec(BaseModel):
    id: UUID
    feature_id: UUID
    task_id: UUID | None
    title: str
    order: int
    content: str
    compiled: bool
    dependencies: list[UUID]


class Execution(BaseModel):
    id: UUID
    task_id: UUID
    spec_id: UUID
    status: str
    failure_reason: str | None
    branch_name: str | None
    started_at: datetime
    completed_at: datetime | None


class ExecutionTrace(BaseModel):
    execution_id: UUID
    task_id: UUID
    spec_id: UUID
    content: str
    started_at: datetime
    created_at: datetime


class ChatSession(BaseModel):
    id: UUID
    session_type: Literal["spec", "feature", "project_chat"]
    context_id: UUID
    context_type: Literal["task", "feature", "project"]
    created_at: datetime
    messages: list[tuple[str, str, str | None, str | None]]


class ChatSessionSummary(BaseModel):
    id: UUID
    created_at: datetime


class TaskSummary(BaseModel):
    id: UUID
    title: str
    status: str
    feature_title: str | None


class FeatureSummary(BaseModel):
    id: UUID
    title: str
    spec_count: int
    compiled_count: int


class ReviewScope(BaseModel):
    project_ids: list[UUID] = []
    include_global: bool


SuggestionTarget = Literal[
    "project_claude_md", "global_claude_md", "ratchet_yaml", "completion_instructions"
]


class ReviewRun(BaseModel):
    id: UUID
    scope: ReviewScope
    started_at: datetime
    completed_at: datetime | None
    suggestion_count: int = 0
    applied_count: int = 0
    dismissed_count: int = 0
    previous_run_id: UUID | None = None


class SuggestionEvidence(BaseModel):
    task_ids: list[UUID] = []
    execution_ids: list[UUID] = []
    trace_excerpts: list[str] = []
    failure_reasons: list[str] = []
    spec_refinement_counts: dict[str, int] = {}
    git_log_excerpts: list[str] = []


class Suggestion(BaseModel):
    id: UUID
    review_run_id: UUID
    order: int
    target: SuggestionTarget
    target_path: str
    title: str
    reasoning: str
    evidence: SuggestionEvidence
    confidence: Literal["low", "medium", "high"]
    priority: Literal["low", "medium", "high"]
    current_content_excerpt: str
    suggested_diff: str
    status: Literal["pending", "applied", "dismissed", "skipped"] = "pending"


@dataclass
class QAExchange:
    question_index: int
    question: str
    answer: str | None        # None if not yet answered
    execution_id: UUID        # which execution asked this
    asked_at: datetime
    answered_at: datetime | None
    answered_by: str | None   # "cli" | "spa"
