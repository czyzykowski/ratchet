"""Unit tests for ProjectManager. Uses InMemoryStore — no database required."""

from __future__ import annotations

from uuid import uuid4

import pytest

from core import events as ev
from core.project_manager import OnboardingError, ProjectManager, validate_repo
from core.store import InMemoryStore


def make_valid_repo(tmp_path):
    """Create minimal directory structure passing onboarding validation."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "CLAUDE.md").write_text("# Test Project")
    (repo / "docs").mkdir()
    (repo / "docs" / "INTENT.md").write_text("# Intent")
    return str(repo)


# ---------------------------------------------------------------------------
# validate_repo — module-level function
# ---------------------------------------------------------------------------


def test_validate_repo_nonexistent_path(tmp_path):
    """should raise OnboardingError when repo path does not exist"""
    missing = str(tmp_path / "does_not_exist")
    with pytest.raises(OnboardingError, match="repo path does not exist"):
        validate_repo(missing)


def test_validate_repo_not_a_git_repo(tmp_path):
    """should raise OnboardingError when path exists but has no .git/"""
    repo = tmp_path / "bare_dir"
    repo.mkdir()
    with pytest.raises(OnboardingError, match="not a git repository"):
        validate_repo(str(repo))


def test_validate_repo_missing_claude_md(tmp_path):
    """should raise OnboardingError when .git/ exists but CLAUDE.md is missing"""
    repo = tmp_path / "no_claude"
    repo.mkdir()
    (repo / ".git").mkdir()
    with pytest.raises(OnboardingError, match="CLAUDE.md not found"):
        validate_repo(str(repo))


def test_validate_repo_missing_intent_md(tmp_path):
    """should raise OnboardingError when CLAUDE.md exists but docs/INTENT.md is missing"""
    repo = tmp_path / "no_intent"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "CLAUDE.md").write_text("# Project")
    (repo / "docs").mkdir()
    with pytest.raises(OnboardingError, match="docs/INTENT.md not found"):
        validate_repo(str(repo))


def test_validate_repo_valid(tmp_path):
    """should return None for a fully valid repo"""
    path = make_valid_repo(tmp_path)
    assert validate_repo(path) is None


# ---------------------------------------------------------------------------
# register_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_project_success(tmp_path):
    """should return Project with correct fields when repo is valid"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)

    project = await manager.register_project(
        name="my-project",
        repo_url=path,
        local_path=path,
    )

    assert project.name == "my-project"
    assert project.repo_url == path
    assert project.local_path == path
    assert project.status == "active"


@pytest.mark.asyncio
async def test_register_project_appends_event(tmp_path):
    """should append PROJECT_CREATED event to store"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)

    project = await manager.register_project(
        name="my-project",
        repo_url=path,
        local_path=path,
    )

    events = await store.get_events(project.id, "project")
    assert len(events) == 1
    assert events[0].event_type == "project.created"


@pytest.mark.asyncio
async def test_register_project_invalid_path(tmp_path):
    """should raise OnboardingError for non-existent path"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    missing = str(tmp_path / "nope")

    with pytest.raises(OnboardingError, match="repo path does not exist"):
        await manager.register_project(name="x", repo_url=missing, local_path=missing)


@pytest.mark.asyncio
async def test_register_project_not_git_repo(tmp_path):
    """should raise OnboardingError when no .git/ directory"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    bare = tmp_path / "bare"
    bare.mkdir()

    with pytest.raises(OnboardingError, match="not a git repository"):
        await manager.register_project(name="x", repo_url=str(bare), local_path=str(bare))


@pytest.mark.asyncio
async def test_register_project_missing_claude_md(tmp_path):
    """should raise OnboardingError when CLAUDE.md is missing"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    with pytest.raises(OnboardingError, match="CLAUDE.md not found"):
        await manager.register_project(name="x", repo_url=str(repo), local_path=str(repo))


@pytest.mark.asyncio
async def test_register_project_missing_intent_md(tmp_path):
    """should raise OnboardingError when docs/INTENT.md is missing"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "CLAUDE.md").write_text("# Project")
    (repo / "docs").mkdir()

    with pytest.raises(OnboardingError, match="docs/INTENT.md not found"):
        await manager.register_project(name="x", repo_url=str(repo), local_path=str(repo))


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_unknown_id(tmp_path):
    """should return None for unknown project_id"""
    from uuid import uuid4

    store = InMemoryStore()
    manager = ProjectManager(store)

    result = await manager.get_project(uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_project_known_id(tmp_path):
    """should return correct Project for known project_id"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)

    project = await manager.register_project(name="known", repo_url=path, local_path=path)

    fetched = await manager.get_project(project.id)
    assert fetched is not None
    assert fetched.id == project.id
    assert fetched.name == "known"


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_projects_empty(tmp_path):
    """should return empty list when no projects registered"""
    store = InMemoryStore()
    manager = ProjectManager(store)

    result = await manager.list_projects()
    assert result == []


@pytest.mark.asyncio
async def test_list_projects_single(tmp_path):
    """should return registered project in list"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)

    project = await manager.register_project(name="one", repo_url=path, local_path=path)

    result = await manager.list_projects()
    assert len(result) == 1
    assert result[0].id == project.id


@pytest.mark.asyncio
async def test_list_projects_two_ordered_by_created_at(tmp_path):
    """should return two projects ordered by created_at ascending"""
    store = InMemoryStore()
    manager = ProjectManager(store)

    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    (repo_a / ".git").mkdir()
    (repo_a / "CLAUDE.md").write_text("")
    (repo_a / "docs").mkdir()
    (repo_a / "docs" / "INTENT.md").write_text("")

    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    (repo_b / ".git").mkdir()
    (repo_b / "CLAUDE.md").write_text("")
    (repo_b / "docs").mkdir()
    (repo_b / "docs" / "INTENT.md").write_text("")

    project_a = await manager.register_project(
        name="alpha", repo_url=str(repo_a), local_path=str(repo_a)
    )
    project_b = await manager.register_project(
        name="beta", repo_url=str(repo_b), local_path=str(repo_b)
    )

    result = await manager.list_projects()
    assert len(result) == 2
    assert result[0].id == project_a.id
    assert result[1].id == project_b.id


# ---------------------------------------------------------------------------
# archive_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_project_removes_from_list(tmp_path):
    """should not appear in list_projects() after archive"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)

    project = await manager.register_project(name="to-archive", repo_url=path, local_path=path)
    await manager.archive_project(project.id)

    result = await manager.list_projects()
    assert result == []


@pytest.mark.asyncio
async def test_archive_project_returns_event(tmp_path):
    """should return PROJECT_ARCHIVED event"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)

    project = await manager.register_project(name="to-archive", repo_url=path, local_path=path)
    event = await manager.archive_project(project.id)

    assert event.event_type == "project.archived"
    assert event.payload["project_id"] == str(project.id)


# ---------------------------------------------------------------------------
# update_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_project_appends_event(tmp_path):
    """should append project.updated event with correct payload"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)
    project = await manager.register_project(name="original", repo_url=path, local_path=path)

    await manager.update_project(
        project.id, name="updated", repo_url="/new/url", local_path="/new/path",
        config_source="db"
    )

    events = await store.get_events(project.id, "project")
    update_events = [e for e in events if e.event_type == ev.PROJECT_UPDATED]
    assert len(update_events) == 1
    assert update_events[0].payload["name"] == "updated"
    assert update_events[0].payload["repo_url"] == "/new/url"
    assert update_events[0].payload["local_path"] == "/new/path"
    assert update_events[0].payload["config_source"] == "db"


@pytest.mark.asyncio
async def test_update_project_dual_writes_to_registry(tmp_path):
    """should append project.updated event to registry aggregate"""
    from uuid import UUID
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)
    project = await manager.register_project(name="original", repo_url=path, local_path=path)

    await manager.update_project(
        project.id, name="updated", repo_url=path, local_path=path
    )

    registry_id = UUID("00000000-0000-0000-0000-000000000001")
    registry_events = await store.get_events(registry_id, "projects")
    update_events = [e for e in registry_events if e.event_type == ev.PROJECT_UPDATED]
    assert len(update_events) == 1
    assert update_events[0].payload["project_id"] == str(project.id)


@pytest.mark.asyncio
async def test_get_project_reflects_update(tmp_path):
    """should return updated name after update_project"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)
    project = await manager.register_project(name="before", repo_url=path, local_path=path)

    await manager.update_project(
        project.id, name="after", repo_url=path, local_path=path
    )

    fetched = await manager.get_project(project.id)
    assert fetched is not None
    assert fetched.name == "after"


@pytest.mark.asyncio
async def test_list_projects_reflects_update(tmp_path):
    """should return updated name in list_projects after update"""
    store = InMemoryStore()
    manager = ProjectManager(store)
    path = make_valid_repo(tmp_path)
    project = await manager.register_project(name="before", repo_url=path, local_path=path)

    await manager.update_project(
        project.id, name="after", repo_url=path, local_path=path
    )

    result = await manager.list_projects()
    assert len(result) == 1
    assert result[0].name == "after"


@pytest.mark.asyncio
async def test_update_project_raises_for_unknown_id():
    """should raise ValueError when project_id not found"""
    store = InMemoryStore()
    manager = ProjectManager(store)

    with pytest.raises(ValueError, match="project not found"):
        await manager.update_project(
            uuid4(), name="x", repo_url="/x", local_path="/x"
        )


@pytest.mark.asyncio
async def test_archive_one_of_two_projects(tmp_path):
    """should return only the active project after archiving one"""
    store = InMemoryStore()
    manager = ProjectManager(store)

    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    (repo_a / ".git").mkdir()
    (repo_a / "CLAUDE.md").write_text("")
    (repo_a / "docs").mkdir()
    (repo_a / "docs" / "INTENT.md").write_text("")

    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    (repo_b / ".git").mkdir()
    (repo_b / "CLAUDE.md").write_text("")
    (repo_b / "docs").mkdir()
    (repo_b / "docs" / "INTENT.md").write_text("")

    project_a = await manager.register_project(
        name="alpha", repo_url=str(repo_a), local_path=str(repo_a)
    )
    project_b = await manager.register_project(
        name="beta", repo_url=str(repo_b), local_path=str(repo_b)
    )

    await manager.archive_project(project_a.id)

    result = await manager.list_projects()
    assert len(result) == 1
    assert result[0].id == project_b.id
