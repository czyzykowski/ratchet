"""Unit tests for review event type constants in core/events."""
from __future__ import annotations

from core import events as ev


def test_should_define_all_six_review_event_type_constants() -> None:
    assert ev.REVIEW_RUN_STARTED == "review_run.started"
    assert ev.REVIEW_RUN_COMPLETED == "review_run.completed"
    assert ev.REVIEW_SUGGESTION_CREATED == "review_suggestion.created"
    assert ev.REVIEW_SUGGESTION_APPLIED == "review_suggestion.applied"
    assert ev.REVIEW_SUGGESTION_DISMISSED == "review_suggestion.dismissed"
    assert ev.REVIEW_SUGGESTION_SKIPPED == "review_suggestion.skipped"


def test_should_have_no_naming_conflicts_with_existing_event_constants() -> None:
    review_constants = {
        ev.REVIEW_RUN_STARTED,
        ev.REVIEW_RUN_COMPLETED,
        ev.REVIEW_SUGGESTION_CREATED,
        ev.REVIEW_SUGGESTION_APPLIED,
        ev.REVIEW_SUGGESTION_DISMISSED,
        ev.REVIEW_SUGGESTION_SKIPPED,
    }
    existing_constants = {
        ev.PROJECT_CREATED, ev.PROJECT_UPDATED, ev.PROJECT_ARCHIVED,
        ev.PROJECT_CONFIG_UPDATED, ev.TASK_CREATED, ev.TASK_STATUS_CHANGED,
        ev.TASK_SPEC_ASSIGNED, ev.TASK_DEPENDENCY_ADDED, ev.TASK_TITLE_UPDATED,
        ev.TASK_TITLE_CHANGED, ev.TASK_BASELINE_QA_FAILED, ev.TASK_BASELINE_QA_RETRY,
        ev.TASK_FORCE_EXECUTE, ev.TASK_DEPLOY_HOOKS_RUN, ev.TASK_WORKER_DISCONNECTED,
        ev.TASK_PR_CREATED, ev.SPEC_CREATED, ev.EXECUTION_STARTED,
        ev.EXECUTION_COMPLETED, ev.EXECUTION_FAILED, ev.TASK_INPUT_REQUESTED,
        ev.TASK_INPUT_PROVIDED, ev.FEATURE_CREATED, ev.HIGH_LEVEL_SPEC_ADDED,
        ev.HIGH_LEVEL_SPEC_COMPILED, ev.CHAT_SESSION_CREATED,
        ev.CHAT_SESSION_MESSAGE_ADDED,
    }
    assert review_constants.isdisjoint(existing_constants)
