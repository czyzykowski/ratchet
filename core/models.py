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
    created_at: datetime
    updated_at: datetime


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


class ChatSession(BaseModel):
    id: UUID
    session_type: Literal["spec", "feature"]
    context_id: UUID
    context_type: Literal["task", "feature"]
    created_at: datetime
    messages: list[tuple[str, str, str | None, str | None]]


@dataclass
class QAExchange:
    question_index: int
    question: str
    answer: str | None        # None if not yet answered
    execution_id: UUID        # which execution asked this
    asked_at: datetime
    answered_at: datetime | None
    answered_by: str | None   # "cli" | "spa"
