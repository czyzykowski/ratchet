"""Regression tests: Pydantic model field sets must match materialized view columns.

These tests document the expected field set for each model so that future drift
between views and models is caught at the unit-test level (no database required).
"""

from __future__ import annotations

from core.models import Execution, Project, Spec, Task


def test_project_fields() -> None:
    """Project model fields must match current_projects view columns."""
    expected = {"id", "name", "repo_url", "local_path", "status", "created_at", "updated_at"}
    assert set(Project.model_fields) == expected


def test_task_fields() -> None:
    """Task model fields must match current_tasks view columns."""
    expected = {
        "id",
        "project_id",
        "title",
        "status",
        "current_spec_id",
        "refinement_count",
        "created_at",
        "updated_at",
        "depends_on",
    }
    assert set(Task.model_fields) == expected


def test_spec_fields() -> None:
    """Spec model fields must match current_specs view columns."""
    expected = {"id", "task_id", "previous_spec_id", "content", "created_at"}
    assert set(Spec.model_fields) == expected


def test_execution_fields() -> None:
    """Execution model fields must match current_executions view columns."""
    expected = {
        "id",
        "task_id",
        "spec_id",
        "status",
        "failure_reason",
        "branch_name",
        "started_at",
        "completed_at",
    }
    assert set(Execution.model_fields) == expected
