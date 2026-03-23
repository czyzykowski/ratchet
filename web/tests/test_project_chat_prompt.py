"""Unit tests for _build_project_chat_prompt."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from web.routes.api.project_chat_sessions import _build_project_chat_prompt


def _make_project(name: str = "my-project") -> MagicMock:
    project = MagicMock()
    project.id = uuid4()
    project.name = name
    project.local_path = "/tmp/test"
    return project


def test_should_include_intent_md_in_prompt() -> None:
    project = _make_project()
    result = _build_project_chat_prompt(project, "# Intent\nDo great things.", "", [], [], [])
    assert "# Intent\nDo great things." in result
    assert "## Project Intent" in result


def test_should_include_claude_md_when_present() -> None:
    project = _make_project()
    guidelines = "# Guidelines\nFollow rules."
    result = _build_project_chat_prompt(project, "intent", guidelines, [], [], [])
    assert "## Project Guidelines" in result
    assert "# Guidelines\nFollow rules." in result


def test_should_omit_claude_md_section_when_empty() -> None:
    project = _make_project()
    result = _build_project_chat_prompt(project, "intent", "", [], [], [])
    assert "## Project Guidelines" not in result


def test_should_include_recent_commits_when_present() -> None:
    project = _make_project()
    commits = ["abc1234 fix: bug fix", "def5678 feat: new feature"]
    result = _build_project_chat_prompt(project, "intent", "", commits, [], [])
    assert "## Recent Activity" in result
    assert "abc1234 fix: bug fix" in result
    assert "def5678 feat: new feature" in result


def test_should_omit_recent_activity_when_empty() -> None:
    project = _make_project()
    result = _build_project_chat_prompt(project, "intent", "", [], [], [])
    assert "## Recent Activity" not in result


def test_should_include_tasks_table_grouped_by_status() -> None:
    project = _make_project()
    tasks = [
        {"title": "Build feature A", "status": "ready_for_spec", "feature_title": None},
        {"title": "Fix bug B", "status": "in_progress", "feature_title": "Feature X"},
        {"title": "Review C", "status": "ready_for_spec", "feature_title": "Feature Y"},
    ]
    result = _build_project_chat_prompt(project, "intent", "", [], tasks, [])
    assert "## Tasks" in result
    assert "Build feature A" in result
    assert "Fix bug B" in result
    assert "Feature X" in result
    assert "in_progress" in result
    assert "ready_for_spec" in result


def test_should_show_no_tasks_message_when_empty() -> None:
    project = _make_project()
    result = _build_project_chat_prompt(project, "intent", "", [], [], [])
    assert "## Tasks" in result
    assert "*No tasks yet.*" in result


def test_should_include_features_table_with_counts() -> None:
    project = _make_project()
    features = [
        {"title": "Auth", "description": "Login flows", "spec_count": 3, "compiled_count": 2},
        {"title": "Dashboard", "description": "UI", "spec_count": 1, "compiled_count": 0},
    ]
    result = _build_project_chat_prompt(project, "intent", "", [], [], features)
    assert "## Features" in result
    assert "Auth" in result
    assert "Dashboard" in result
    assert "| 3 | 2 |" in result
    assert "| 1 | 0 |" in result


def test_should_show_no_features_message_when_empty() -> None:
    project = _make_project()
    result = _build_project_chat_prompt(project, "intent", "", [], [], [])
    assert "## Features" in result
    assert "*No features yet.*" in result


def test_should_include_capabilities_with_project_name() -> None:
    project = _make_project("awesome-project")
    result = _build_project_chat_prompt(project, "intent", "", [], [], [])
    assert "## Capabilities" in result
    assert "awesome-project" in result
    assert "project assistant" in result


def test_should_handle_task_with_feature_title_none() -> None:
    project = _make_project()
    tasks = [{"title": "Standalone task", "status": "ready_for_spec", "feature_title": None}]
    result = _build_project_chat_prompt(project, "intent", "", [], tasks, [])
    assert "Standalone task" in result
