"""Unit tests for ReviewEngine."""

from __future__ import annotations

import io
import json
from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from core.models import ReviewRun, ReviewScope, Suggestion
from core.review_collector import CollectedData
from core.review_engine import ReviewAnalysisError, ReviewEngine

FIXED_RUN_ID = UUID("00000000-0000-0000-0000-000000000001")

PATCH_POPEN = "core.claude_subprocess.subprocess.Popen"


@pytest.fixture
def review_run() -> ReviewRun:
    return ReviewRun(
        id=FIXED_RUN_ID,
        scope=ReviewScope(project_ids=[], include_global=False),
        started_at=datetime(2026, 1, 1),
        completed_at=None,
    )


@pytest.fixture
def collected_data() -> CollectedData:
    return CollectedData(
        tasks=[],
        specs_by_task={},
        executions_by_task={},
        trace_contents={},
        qa_failures=[],
        status_histories={},
        git_log={},
        project_claude_md={},
        global_claude_md="",
        ratchet_yaml_content=None,
        completion_instructions_content="## Completion Instructions\nCOMPLETED or BLOCKED",
    )


def _make_item(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "target": "ratchet_yaml",
        "target_path": "/path/to/ratchet.yaml",
        "title": "Fix something",
        "reasoning": "Because reasons",
        "evidence": {
            "task_ids": [],
            "execution_ids": [],
            "trace_excerpts": [],
            "failure_reasons": [],
            "spec_refinement_counts": {},
            "git_log_excerpts": [],
        },
        "confidence": "high",
        "priority": "medium",
        "current_content_excerpt": "old content",
        "suggested_diff": "diff here",
    }
    base.update(overrides)
    return base


def _fake_popen(stdout: str = "", returncode: int = 0) -> MagicMock:
    mock = MagicMock()
    mock.stdin = MagicMock()
    mock.stdout = io.StringIO(stdout)
    mock.stderr = io.StringIO("")
    mock.wait.return_value = returncode
    mock.returncode = returncode
    return mock


class TestReviewEngine:
    def test_parse_valid_json(
        self, review_run: ReviewRun, collected_data: CollectedData
    ) -> None:
        items = [_make_item(title="First"), _make_item(title="Second")]
        with patch(PATCH_POPEN, return_value=_fake_popen(json.dumps(items))):
            engine = ReviewEngine()
            result = engine.analyze(review_run, collected_data, "")
        assert len(result) == 2
        assert all(isinstance(s, Suggestion) for s in result)
        assert all(s.review_run_id == FIXED_RUN_ID for s in result)

    def test_strip_markdown_fences(
        self, review_run: ReviewRun, collected_data: CollectedData
    ) -> None:
        item = _make_item()
        raw = f"```json\n{json.dumps([item])}\n```"
        with patch(PATCH_POPEN, return_value=_fake_popen(raw)):
            engine = ReviewEngine()
            result = engine.analyze(review_run, collected_data, "")
        assert len(result) == 1
        assert isinstance(result[0], Suggestion)

    def test_raise_on_invalid_json(
        self, review_run: ReviewRun, collected_data: CollectedData
    ) -> None:
        with patch(PATCH_POPEN, return_value=_fake_popen("not valid json")):
            engine = ReviewEngine()
            with pytest.raises(ReviewAnalysisError):
                engine.analyze(review_run, collected_data, "")

    def test_sequential_order(
        self, review_run: ReviewRun, collected_data: CollectedData
    ) -> None:
        items = [_make_item(title=f"Item {i}") for i in range(3)]
        with patch(PATCH_POPEN, return_value=_fake_popen(json.dumps(items))):
            engine = ReviewEngine()
            result = engine.analyze(review_run, collected_data, "")
        assert [s.order for s in result] == [0, 1, 2]

    def test_status_pending(
        self, review_run: ReviewRun, collected_data: CollectedData
    ) -> None:
        items = [_make_item(status="applied") for _ in range(3)]
        with patch(PATCH_POPEN, return_value=_fake_popen(json.dumps(items))):
            engine = ReviewEngine()
            result = engine.analyze(review_run, collected_data, "")
        assert all(s.status == "pending" for s in result)
