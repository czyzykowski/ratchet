from __future__ import annotations

import uuid
from datetime import UTC, datetime

from core.models import ReviewRun, ReviewScope, Suggestion, SuggestionEvidence


def test_should_serialize_and_deserialize_review_run_with_nested_scope():
    project_id = uuid.uuid4()
    run = ReviewRun(
        id=uuid.uuid4(),
        scope=ReviewScope(project_ids=[project_id], include_global=True),
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=None,
        suggestion_count=3,
        applied_count=1,
        dismissed_count=2,
        previous_run_id=None,
    )
    data = run.model_dump()
    restored = ReviewRun.model_validate(data)
    assert restored.id == run.id
    assert restored.scope.project_ids == [project_id]
    assert restored.scope.include_global is True
    assert restored.suggestion_count == 3


def test_should_serialize_and_deserialize_suggestion_with_all_evidence_fields():
    task_id = uuid.uuid4()
    exec_id = uuid.uuid4()
    evidence = SuggestionEvidence(
        task_ids=[task_id],
        execution_ids=[exec_id],
        trace_excerpts=["trace line 1"],
        failure_reasons=["reason A"],
        spec_refinement_counts={"task-1": 2},
        git_log_excerpts=["abc123 fix: something"],
    )
    suggestion = Suggestion(
        id=uuid.uuid4(),
        review_run_id=uuid.uuid4(),
        order=1,
        target="project_claude_md",
        target_path=".claude/CLAUDE.md",
        title="Update CLAUDE.md",
        reasoning="Based on observed patterns",
        evidence=evidence,
        confidence="high",
        priority="medium",
        current_content_excerpt="old content",
        suggested_diff="+ new line",
    )
    data = suggestion.model_dump()
    restored = Suggestion.model_validate(data)
    assert restored.evidence.spec_refinement_counts == {"task-1": 2}
    assert restored.evidence.trace_excerpts == ["trace line 1"]
    assert restored.evidence.git_log_excerpts == ["abc123 fix: something"]


def test_should_default_suggestion_status_to_pending():
    suggestion = Suggestion(
        id=uuid.uuid4(),
        review_run_id=uuid.uuid4(),
        order=1,
        target="global_claude_md",
        target_path="/global/CLAUDE.md",
        title="Some suggestion",
        reasoning="Some reasoning",
        evidence=SuggestionEvidence(),
        confidence="low",
        priority="low",
        current_content_excerpt="",
        suggested_diff="",
    )
    assert suggestion.status == "pending"
