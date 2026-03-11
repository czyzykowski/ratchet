"""Unit tests for DB-backed project config features. Uses InMemoryStore — no database required."""

from __future__ import annotations

import textwrap
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

import pytest

from core import events as ev
from core.context_assembler import ContextAssembler, ContextAssemblyError, read_intent
from core.execution_manager import prepare_task_environment
from core.project_manager import OnboardingError, ProjectManager, validate_repo
from core.qa_runner import load_qa_config
from core.store import InMemoryStore

# ---------------------------------------------------------------------------
# validate_repo — config_source="db" skips file checks
# ---------------------------------------------------------------------------


def test_validate_repo_db_skips_claude_md_check(tmp_path: Path) -> None:
    """should not raise when CLAUDE.md is missing and config_source='db'"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    # No CLAUDE.md, no docs/INTENT.md
    validate_repo(str(repo), config_source="db")  # should not raise


def test_validate_repo_db_skips_intent_md_check(tmp_path: Path) -> None:
    """should not raise when docs/INTENT.md is missing and config_source='db'"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "CLAUDE.md").write_text("# Project")
    # No docs/INTENT.md
    validate_repo(str(repo), config_source="db")  # should not raise


def test_validate_repo_db_still_requires_git(tmp_path: Path) -> None:
    """should still raise when .git/ is missing even with config_source='db'"""
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(OnboardingError, match="not a git repository"):
        validate_repo(str(repo), config_source="db")


def test_validate_repo_db_still_requires_path_exists(tmp_path: Path) -> None:
    """should still raise when path does not exist even with config_source='db'"""
    missing = str(tmp_path / "does_not_exist")
    with pytest.raises(OnboardingError, match="repo path does not exist"):
        validate_repo(missing, config_source="db")


def test_validate_repo_disk_still_checks_claude_md(tmp_path: Path) -> None:
    """disk config_source should still raise for missing CLAUDE.md"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    with pytest.raises(OnboardingError, match="CLAUDE.md not found"):
        validate_repo(str(repo), config_source="disk")


# ---------------------------------------------------------------------------
# update_project_config — appends correct event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_project_config_appends_event(tmp_path: Path) -> None:
    """should append PROJECT_CONFIG_UPDATED event with correct payload"""
    store = InMemoryStore()
    pm = ProjectManager(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    project = await pm.register_project(
        name="test", repo_url=str(repo), local_path=str(repo), config_source="db"
    )

    await pm.update_project_config(
        project_id=project.id,
        claude_md="# Claude\nInstructions",
        intent_md="# Intent\nProject purpose",
        ratchet_yaml="qa:\n  steps:\n    test: pytest\n",
    )

    events = await store.get_events(project.id, "project")
    config_events = [e for e in events if e.event_type == ev.PROJECT_CONFIG_UPDATED]
    assert len(config_events) == 1
    p = config_events[0].payload
    assert p["project_id"] == str(project.id)
    assert p["claude_md"] == "# Claude\nInstructions"
    assert p["intent_md"] == "# Intent\nProject purpose"
    assert p["ratchet_yaml"] == "qa:\n  steps:\n    test: pytest\n"


@pytest.mark.asyncio
async def test_update_project_config_dual_written_to_registry(tmp_path: Path) -> None:
    """should dual-write PROJECT_CONFIG_UPDATED to projects registry"""
    from uuid import UUID

    store = InMemoryStore()
    pm = ProjectManager(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    project = await pm.register_project(
        name="test", repo_url=str(repo), local_path=str(repo), config_source="db"
    )

    await pm.update_project_config(
        project_id=project.id,
        claude_md="# Claude",
        intent_md="# Intent",
        ratchet_yaml=None,
    )

    _PROJECTS_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")
    registry_events = await store.get_events(_PROJECTS_REGISTRY_ID, "projects")
    config_events = [e for e in registry_events if e.event_type == ev.PROJECT_CONFIG_UPDATED]
    assert len(config_events) == 1


# ---------------------------------------------------------------------------
# get_project — replays config event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_replays_config_event(tmp_path: Path) -> None:
    """should populate claude_md, intent_md, ratchet_yaml after config update"""
    store = InMemoryStore()
    pm = ProjectManager(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    project = await pm.register_project(
        name="test", repo_url=str(repo), local_path=str(repo), config_source="db"
    )

    await pm.update_project_config(
        project_id=project.id,
        claude_md="# Claude",
        intent_md="# Intent",
        ratchet_yaml="qa:\n  steps: {}\n",
    )

    fetched = await pm.get_project(project.id)
    assert fetched is not None
    assert fetched.config_source == "db"
    assert fetched.claude_md == "# Claude"
    assert fetched.intent_md == "# Intent"
    assert fetched.ratchet_yaml == "qa:\n  steps: {}\n"


@pytest.mark.asyncio
async def test_get_project_config_source_disk_by_default(tmp_path: Path) -> None:
    """should default to config_source='disk' for old events without the field"""
    store = InMemoryStore()
    # Manually append a PROJECT_CREATED event without config_source in payload
    from uuid import uuid4

    project_id = uuid4()
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project",
        event_type=ev.PROJECT_CREATED,
        payload={
            "project_id": str(project_id),
            "name": "old-project",
            "repo_url": "/path",
            "local_path": "/path",
            "status": "active",
            # no config_source field
        },
    )

    pm = ProjectManager(store)
    project = await pm.get_project(project_id)
    assert project is not None
    assert project.config_source == "disk"
    assert project.claude_md is None


@pytest.mark.asyncio
async def test_list_projects_replays_config_event(tmp_path: Path) -> None:
    """list_projects should include config fields after config update"""
    store = InMemoryStore()
    pm = ProjectManager(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    project = await pm.register_project(
        name="test", repo_url=str(repo), local_path=str(repo), config_source="db"
    )

    await pm.update_project_config(
        project_id=project.id,
        claude_md="# Claude content",
        intent_md=None,
        ratchet_yaml=None,
    )

    projects = await pm.list_projects()
    assert len(projects) == 1
    assert projects[0].claude_md == "# Claude content"
    assert projects[0].config_source == "db"


# ---------------------------------------------------------------------------
# read_intent — override bypasses disk
# ---------------------------------------------------------------------------


def test_read_intent_returns_override_without_touching_disk(tmp_path: Path) -> None:
    """should return intent_md immediately when provided, skipping disk read"""
    # tmp_path has no INTENT.md — if disk were read it would raise
    worktree = str(tmp_path)
    result = read_intent(worktree, intent_md="# Override Intent\nContent here.")
    assert result == "# Override Intent\nContent here."


def test_read_intent_none_override_reads_disk(tmp_path: Path) -> None:
    """should read from disk when intent_md=None"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "INTENT.md").write_text("# Disk Intent")
    result = read_intent(str(tmp_path), intent_md=None)
    assert result == "# Disk Intent"


def test_read_intent_raises_for_missing_file_when_no_override(tmp_path: Path) -> None:
    """should raise ContextAssemblyError when file missing and no override"""
    worktree = tmp_path / "empty"
    worktree.mkdir()
    with pytest.raises(ContextAssemblyError, match="INTENT.md"):
        read_intent(str(worktree), intent_md=None)


# ---------------------------------------------------------------------------
# prepare_task_environment — writes CLAUDE.md when claude_md provided
# ---------------------------------------------------------------------------


def test_prepare_task_environment_writes_claude_md(tmp_path: Path) -> None:
    """should write CLAUDE.md to worktree when claude_md is provided"""
    import os
    import uuid

    execution_id = uuid.uuid4()
    repo_path = str(tmp_path)
    claude_content = "# Project Instructions\nDo the thing."

    def _fake_git_worktree(cmd, **kwargs):
        # simulate git worktree add creating the directory
        worktree_dir = os.path.join(repo_path, ".worktrees", str(execution_id))
        os.makedirs(worktree_dir, exist_ok=True)

    with patch("core.execution_manager.subprocess.run", side_effect=_fake_git_worktree):
        worktree_path = prepare_task_environment(repo_path, execution_id, claude_md=claude_content)

    claude_file = Path(worktree_path) / "CLAUDE.md"
    assert claude_file.exists()
    assert claude_file.read_text() == claude_content


def test_prepare_task_environment_no_claude_md_when_none(tmp_path: Path) -> None:
    """should not create CLAUDE.md when claude_md is None"""
    import os
    import uuid

    execution_id = uuid.uuid4()
    repo_path = str(tmp_path)

    def _fake_git_worktree(cmd, **kwargs):
        worktree_dir = os.path.join(repo_path, ".worktrees", str(execution_id))
        os.makedirs(worktree_dir, exist_ok=True)

    with patch("core.execution_manager.subprocess.run", side_effect=_fake_git_worktree):
        worktree_path = prepare_task_environment(repo_path, execution_id, claude_md=None)

    claude_file = Path(worktree_path) / "CLAUDE.md"
    assert not claude_file.exists()


# ---------------------------------------------------------------------------
# load_qa_config — parses override string
# ---------------------------------------------------------------------------


def test_load_qa_config_parses_ratchet_yaml_override(tmp_path: Path) -> None:
    """should parse ratchet_yaml string directly when provided"""
    yaml_content = textwrap.dedent("""\
    qa:
      max_fix_attempts: 2
      steps:
        test: "pytest -v"
        lint: "ruff check ."
    """)
    config = load_qa_config(str(tmp_path), ratchet_yaml=yaml_content)
    assert config is not None
    assert config.max_fix_attempts == 2
    assert len(config.steps) == 2
    assert config.steps[0].name == "test"
    assert config.steps[0].command == "pytest -v"


def test_load_qa_config_override_skips_disk_read(tmp_path: Path) -> None:
    """should not read disk file when ratchet_yaml override is provided"""
    # Write a DIFFERENT config to disk — it should be ignored
    (tmp_path / "ratchet.yaml").write_text("qa:\n  steps:\n    disk_step: echo disk\n")
    yaml_content = "qa:\n  steps:\n    override_step: echo override\n"
    config = load_qa_config(str(tmp_path), ratchet_yaml=yaml_content)
    assert config is not None
    assert config.steps[0].name == "override_step"


def test_load_qa_config_override_empty_returns_none(tmp_path: Path) -> None:
    """should return None when ratchet_yaml override has no qa section"""
    config = load_qa_config(str(tmp_path), ratchet_yaml="other:\n  key: value\n")
    assert config is None


# ---------------------------------------------------------------------------
# ContextAssembler — uses project.intent_md when config_source="db"
# ---------------------------------------------------------------------------


async def _seed_store_for_config_test(
    store: InMemoryStore,
    worktree_path: str,
    spec_content: str = "# My Spec",
) -> tuple:
    """Seed store with EXECUTION_STARTED and SPEC_CREATED events."""
    import uuid

    execution_id = uuid.uuid4()
    task_id = uuid.uuid4()
    spec_id = uuid.uuid4()

    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload={
            "execution_id": str(execution_id),
            "task_id": str(task_id),
            "spec_id": str(spec_id),
            "worktree_path": worktree_path,
            "status": "running",
        },
    )
    await store.append_event(
        aggregate_id=spec_id,
        aggregate_type="spec",
        event_type=ev.SPEC_CREATED,
        payload={
            "spec_id": str(spec_id),
            "task_id": str(task_id),
            "content": spec_content,
            "previous_spec_id": None,
        },
    )
    return execution_id, task_id, spec_id


@pytest.mark.asyncio
async def test_assemble_uses_project_intent_md_when_db_config(tmp_path: Path) -> None:
    """should use project.intent_md without reading disk when config_source='db'"""
    from datetime import datetime
    from uuid import uuid4

    from core.models import Project

    store = InMemoryStore()
    # worktree has no INTENT.md — would raise if disk were read
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    execution_id, task_id, spec_id = await _seed_store_for_config_test(
        store, str(worktree)
    )

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        name="test-project",
        repo_url="/tmp/repo",
        local_path="/tmp/repo",
        status="active",
        config_source="db",
        intent_md="# DB Intent\nProject purpose from DB.",
        created_at=now,
        updated_at=now,
    )

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id, project)

    assert "# DB Intent" in ctx.prompt
    assert "Project purpose from DB." in ctx.prompt


@pytest.mark.asyncio
async def test_assemble_reads_disk_when_config_source_disk(tmp_path: Path) -> None:
    """should read INTENT.md from disk when config_source='disk'"""
    from datetime import datetime
    from uuid import uuid4

    from core.models import Project

    store = InMemoryStore()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "docs").mkdir()
    (worktree / "docs" / "INTENT.md").write_text("# Disk Intent")

    execution_id, task_id, spec_id = await _seed_store_for_config_test(
        store, str(worktree)
    )

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        name="test-project",
        repo_url="/tmp/repo",
        local_path="/tmp/repo",
        status="active",
        config_source="disk",
        intent_md="# DB Intent (should be ignored)",
        created_at=now,
        updated_at=now,
    )

    assembler = ContextAssembler(store)
    ctx = await assembler.assemble(execution_id, project)

    assert "# Disk Intent" in ctx.prompt
    assert "should be ignored" not in ctx.prompt
