"""Unit tests for core.event_queries — no database required."""

from __future__ import annotations

from types import SimpleNamespace

from core import events as ev
from core.event_queries import has_pending_baseline_qa_failure


def _evt(event_type: str, sequence: int) -> SimpleNamespace:
    return SimpleNamespace(event_type=event_type, sequence=sequence)


def test_no_failure_returns_false() -> None:
    """should return False when no baseline QA failure event exists."""
    events = [_evt(ev.TASK_CREATED, 1)]
    assert has_pending_baseline_qa_failure(events) is False


def test_empty_events_returns_false() -> None:
    """should return False when event list is empty."""
    assert has_pending_baseline_qa_failure([]) is False


def test_failure_with_no_retry_returns_true() -> None:
    """should return True when baseline QA failed and no retry or force-execute follows."""
    events = [
        _evt(ev.TASK_CREATED, 1),
        _evt(ev.TASK_BASELINE_QA_FAILED, 2),
    ]
    assert has_pending_baseline_qa_failure(events) is True


def test_failure_cleared_by_retry_returns_false() -> None:
    """should return False when baseline QA retry follows the failure."""
    events = [
        _evt(ev.TASK_BASELINE_QA_FAILED, 2),
        _evt(ev.TASK_BASELINE_QA_RETRY, 3),
    ]
    assert has_pending_baseline_qa_failure(events) is False


def test_failure_cleared_by_force_execute_returns_false() -> None:
    """should return False when force-execute follows the failure."""
    events = [
        _evt(ev.TASK_BASELINE_QA_FAILED, 2),
        _evt(ev.TASK_FORCE_EXECUTE, 3),
    ]
    assert has_pending_baseline_qa_failure(events) is False


def test_new_failure_after_retry_returns_true() -> None:
    """should return True when a new failure occurs after a retry."""
    events = [
        _evt(ev.TASK_BASELINE_QA_FAILED, 2),
        _evt(ev.TASK_BASELINE_QA_RETRY, 3),
        _evt(ev.TASK_BASELINE_QA_FAILED, 4),
    ]
    assert has_pending_baseline_qa_failure(events) is True


def test_multiple_failures_last_is_pending_returns_true() -> None:
    """should return True when the last failure has no corresponding retry."""
    events = [
        _evt(ev.TASK_BASELINE_QA_FAILED, 1),
        _evt(ev.TASK_BASELINE_QA_RETRY, 2),
        _evt(ev.TASK_BASELINE_QA_FAILED, 3),
        _evt(ev.TASK_BASELINE_QA_RETRY, 4),
        _evt(ev.TASK_BASELINE_QA_FAILED, 5),
    ]
    assert has_pending_baseline_qa_failure(events) is True
