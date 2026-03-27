"""Unit tests for web.action_executor."""

from __future__ import annotations

import pathlib
from uuid import UUID, uuid4

import pytest

from core import events as ev
from core.feature_manager import FeatureManager
from core.project_manager import ProjectManager
from core.state_machine import TaskStateMachine
from core.store import InMemoryStore
from core.task_manager import TaskManager
from web.action_executor import execute_action
from web.action_parser import ParsedAction


def _make_parsed(action: str, **payload: object) -> ParsedAction:
    return ParsedAction(action=action, payload=dict(payload), start=0, end=0)


async def _seed_task(store: InMemoryStore, project_id: object) -> object:
    pid = UUID(str(project_id))
    task = await TaskManager(store).create_task(pid, "Seeded task")
    return task


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def project_id() -> object:
    return uuid4()


@pytest.mark.asyncio
async def test_should_create_task_via_action(
    store: InMemoryStore, project_id: object
) -> None:
    parsed = _make_parsed("create_task", title="New Task")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert result.action == "create_task"
    assert "New Task" in result.message
    assert result.entity_id is not None

    task_id = UUID(result.entity_id)
    task = await TaskManager(store).get_task(task_id)
    assert task is not None
    assert task.title == "New Task"


@pytest.mark.asyncio
async def test_should_create_feature_via_action(
    store: InMemoryStore, project_id: object
) -> None:
    parsed = _make_parsed(
        "create_feature", title="Auth Feature", description="Handles auth flows"
    )
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert result.action == "create_feature"
    assert "Auth Feature" in result.message
    assert result.entity_id is not None

    feature_id = UUID(result.entity_id)
    feature = await FeatureManager(store).get_feature(feature_id)
    assert feature is not None
    assert feature.title == "Auth Feature"
    assert feature.description == "Handles auth flows"


@pytest.mark.asyncio
async def test_should_update_task_title(
    store: InMemoryStore, project_id: object
) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("update_task", task_id=task_id, title="Renamed Task")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert "title" in result.message

    task_events = await store.get_events(UUID(task_id), "task")
    title_events = [e for e in task_events if e.event_type == ev.TASK_TITLE_UPDATED]
    assert len(title_events) == 1
    assert title_events[0].payload["title"] == "Renamed Task"


@pytest.mark.asyncio
async def test_should_update_task_status(
    store: InMemoryStore, project_id: object
) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("update_task", task_id=task_id, status=ev.ABANDONED)
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert "status" in result.message

    task_events = await store.get_events(UUID(task_id), "task")
    status_events = [e for e in task_events if e.event_type == ev.TASK_STATUS_CHANGED]
    assert len(status_events) == 1
    assert status_events[0].payload["to_status"] == ev.ABANDONED


@pytest.mark.asyncio
async def test_should_archive_task(store: InMemoryStore, project_id: object) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("archive_task", task_id=task_id, reason="No longer needed")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is True
    assert result.action == "archive_task"
    assert result.entity_id == task_id

    sm = TaskStateMachine(store)
    status = await sm.get_current_status(UUID(task_id))
    assert status == ev.ABANDONED


@pytest.mark.asyncio
async def test_should_return_error_for_unknown_action(
    store: InMemoryStore, project_id: object
) -> None:
    parsed = _make_parsed("do_something_weird")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is False
    assert result.error is not None
    assert "Unknown action" in result.error


@pytest.mark.asyncio
async def test_should_return_error_for_invalid_transition(
    store: InMemoryStore, project_id: object
) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    # ready_for_spec cannot go to merged directly
    parsed = _make_parsed("update_task", task_id=task_id, status=ev.DEPLOYED)
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_should_return_error_for_missing_required_fields(
    store: InMemoryStore, project_id: object
) -> None:
    # create_task without title
    parsed = _make_parsed("create_task")
    result = await execute_action(parsed, store, UUID(str(project_id)))
    assert result.success is False
    assert result.error is not None

    # archive_task without task_id
    parsed2 = _make_parsed("archive_task")
    result2 = await execute_action(parsed2, store, UUID(str(project_id)))
    assert result2.success is False
    assert result2.error is not None


@pytest.mark.asyncio
async def test_should_add_hls_to_feature(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    feature = await FeatureManager(store).create_feature(pid, "My Feature", "desc")
    feature_id = str(feature.id)

    parsed = _make_parsed(
        "add_hls",
        feature_id=feature_id,
        title="Spec One",
        order=1,
        content="Do the thing",
    )
    result = await execute_action(parsed, store, pid)

    assert result.success is True
    assert result.action == "add_hls"
    assert "Spec One" in result.message
    assert result.entity_id is not None

    specs = await FeatureManager(store).get_high_level_specs(feature.id)
    assert len(specs) == 1
    assert specs[0].title == "Spec One"
    assert specs[0].order == 1
    assert specs[0].content == "Do the thing"


@pytest.mark.asyncio
async def test_should_add_hls_with_dependencies(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    fm = FeatureManager(store)
    feature = await fm.create_feature(pid, "My Feature", "desc")
    hls1 = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content 1", [])
    hls2 = await fm.add_high_level_spec(feature.id, "Spec 2", 2, "content 2", [])

    parsed = _make_parsed(
        "add_hls",
        feature_id=str(feature.id),
        title="Spec 3",
        order=3,
        content="content 3",
        dependencies=[1, 2],
    )
    result = await execute_action(parsed, store, pid)

    assert result.success is True
    specs = await fm.get_high_level_specs(feature.id)
    spec3 = next(s for s in specs if s.order == 3)
    assert set(spec3.dependencies) == {hls1.id, hls2.id}


@pytest.mark.asyncio
async def test_should_fail_add_hls_when_missing_required_fields(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    feature = await FeatureManager(store).create_feature(pid, "F", "d")
    fid = str(feature.id)

    # missing feature_id
    r = await execute_action(_make_parsed("add_hls", title="T", order=1, content="C"), store, pid)
    assert r.success is False

    # missing title
    parsed_no_title = _make_parsed("add_hls", feature_id=fid, order=1, content="C")
    r = await execute_action(parsed_no_title, store, pid)
    assert r.success is False

    # missing order
    parsed_no_order = _make_parsed("add_hls", feature_id=fid, title="T", content="C")
    r = await execute_action(parsed_no_order, store, pid)
    assert r.success is False

    # missing content
    parsed_no_content = _make_parsed("add_hls", feature_id=fid, title="T", order=1)
    r = await execute_action(parsed_no_content, store, pid)
    assert r.success is False


@pytest.mark.asyncio
async def test_should_fail_add_hls_with_invalid_dependency_order(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    fm = FeatureManager(store)
    feature = await fm.create_feature(pid, "F", "d")
    await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])

    parsed = _make_parsed(
        "add_hls",
        feature_id=str(feature.id),
        title="Spec 2",
        order=2,
        content="content",
        dependencies=[99],
    )
    result = await execute_action(parsed, store, pid)

    assert result.success is False
    assert result.error is not None
    assert "99" in result.error


@pytest.mark.asyncio
async def test_should_add_hls_with_empty_dependencies(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    feature = await FeatureManager(store).create_feature(pid, "F", "d")

    parsed = _make_parsed(
        "add_hls",
        feature_id=str(feature.id),
        title="Spec 1",
        order=1,
        content="content",
        dependencies=[],
    )
    result = await execute_action(parsed, store, pid)

    assert result.success is True
    specs = await FeatureManager(store).get_high_level_specs(feature.id)
    assert specs[0].dependencies == []


# --- register_project tests ---


@pytest.mark.asyncio
async def test_should_register_project_via_action(
    store: InMemoryStore, tmp_path: pathlib.Path
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "CLAUDE.md").write_text("# claude")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "INTENT.md").write_text("# intent")

    parsed = _make_parsed("register_project", name="my-project", path=str(tmp_path))
    result = await execute_action(parsed, store, None)

    assert result.success is True
    assert result.action == "register_project"
    assert result.entity_id is not None
    project = await ProjectManager(store).get_project(UUID(result.entity_id))
    assert project is not None
    assert project.name == "my-project"


@pytest.mark.asyncio
async def test_should_register_project_with_db_config_source(
    store: InMemoryStore, tmp_path: pathlib.Path
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "CLAUDE.md").write_text("claude content")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "INTENT.md").write_text("intent content")
    (tmp_path / "ratchet.yaml").write_text("yaml content")

    parsed = _make_parsed(
        "register_project",
        name="db-project",
        path=str(tmp_path),
        config_source="db",
    )
    result = await execute_action(parsed, store, None)

    assert result.success is True
    project_id = UUID(result.entity_id)  # type: ignore[arg-type]
    project_events = await store.get_events(project_id, "project")
    config_events = [e for e in project_events if e.event_type == ev.PROJECT_CONFIG_UPDATED]
    assert len(config_events) == 1
    assert config_events[0].payload["claude_md"] == "claude content"
    assert config_events[0].payload["intent_md"] == "intent content"
    assert config_events[0].payload["ratchet_yaml"] == "yaml content"


@pytest.mark.asyncio
async def test_should_register_project_and_update_session_context(
    store: InMemoryStore, tmp_path: pathlib.Path
) -> None:
    (tmp_path / ".git").mkdir()
    session_id = uuid4()
    await store.append_event(
        aggregate_id=session_id,
        aggregate_type="chat_session",
        event_type=ev.CHAT_SESSION_CREATED,
        payload={"session_type": "bootstrap"},
    )

    parsed = _make_parsed(
        "register_project", name="ctx-project", path=str(tmp_path), config_source="db"
    )
    result = await execute_action(parsed, store, None, session_id=session_id)

    assert result.success is True
    session_events = await store.get_events(session_id, "chat_session")
    context_events = [e for e in session_events if e.event_type == ev.CHAT_SESSION_CONTEXT_UPDATED]
    assert len(context_events) == 1
    assert context_events[0].payload["context_id"] == result.entity_id
    assert context_events[0].payload["context_type"] == "project"


@pytest.mark.asyncio
async def test_should_fail_register_project_when_path_missing(
    store: InMemoryStore,
) -> None:
    parsed = _make_parsed("register_project", name="bad-project", path="/nonexistent/path/xyz")
    result = await execute_action(parsed, store, None)

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_should_fail_register_project_when_name_missing(
    store: InMemoryStore, tmp_path: pathlib.Path
) -> None:
    (tmp_path / ".git").mkdir()
    parsed = _make_parsed("register_project", path=str(tmp_path))
    result = await execute_action(parsed, store, None)

    assert result.success is False
    assert result.error is not None


# --- check_task_status tests ---


@pytest.mark.asyncio
async def test_should_check_task_status(
    store: InMemoryStore, project_id: object
) -> None:
    task = await _seed_task(store, project_id)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("check_task_status", task_id=task_id)
    result = await execute_action(parsed, store, None)

    assert result.success is True
    assert result.action == "check_task_status"
    assert "ready_for_spec" in result.message
    assert result.entity_id == task_id


@pytest.mark.asyncio
async def test_should_check_task_status_blocked_with_reason(
    store: InMemoryStore, project_id: object
) -> None:
    task = await _seed_task(store, project_id)
    task_id = task.id  # type: ignore[attr-defined]
    sm = TaskStateMachine(store)
    # Transition through valid path to blocked
    await sm.transition(task_id, ev.SPEC_QA)
    await sm.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
    await sm.transition(task_id, ev.IN_PROGRESS)
    await sm.transition(task_id, ev.BLOCKED, extra_payload={"failure_reason": "tests failed"})

    parsed = _make_parsed("check_task_status", task_id=str(task_id))
    result = await execute_action(parsed, store, None)

    assert result.success is True
    assert "blocked" in result.message
    assert "tests failed" in result.message


@pytest.mark.asyncio
async def test_should_fail_check_task_status_when_task_not_found(
    store: InMemoryStore,
) -> None:
    parsed = _make_parsed("check_task_status", task_id=str(uuid4()))
    result = await execute_action(parsed, store, None)

    assert result.success is False
    assert result.error is not None
    assert "task not found" in result.error


@pytest.mark.asyncio
async def test_should_fail_check_task_status_when_task_id_missing(
    store: InMemoryStore,
) -> None:
    parsed = _make_parsed("check_task_status")
    result = await execute_action(parsed, store, None)

    assert result.success is False
    assert result.error is not None


# --- add_spec tests ---


async def _seed_task_in_status(
    store: InMemoryStore, project_id: UUID, status: str
) -> object:
    """Create a task and advance it to the given status."""
    task = await TaskManager(store).create_task(project_id, "Test task")
    sm = TaskStateMachine(store)
    # Advance through states to reach target
    if status == ev.READY_FOR_SPEC:
        pass  # default
    elif status == ev.SPEC_QA:
        await sm.transition(task.id, ev.SPEC_QA)
    elif status == ev.READY_FOR_IMPLEMENTATION:
        await sm.transition(task.id, ev.SPEC_QA)
        await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
    elif status == ev.IN_PROGRESS:
        await sm.transition(task.id, ev.SPEC_QA)
        await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
        await sm.transition(task.id, ev.IN_PROGRESS)
    elif status == ev.BLOCKED:
        await sm.transition(task.id, ev.SPEC_QA)
        await sm.transition(task.id, ev.READY_FOR_IMPLEMENTATION)
        await sm.transition(task.id, ev.IN_PROGRESS)
        await sm.transition(task.id, ev.BLOCKED)
    return task


@pytest.mark.asyncio
async def test_should_add_spec_from_ready_for_spec(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    task = await _seed_task_in_status(store, pid, ev.READY_FOR_SPEC)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("add_spec", task_id=task_id, content="## Spec\nDo the thing.")
    result = await execute_action(parsed, store, pid)

    assert result.success is True
    assert result.action == "add_spec"
    assert task_id in result.message
    assert result.entity_id is not None

    # Verify task transitioned to ready_for_implementation
    sm = TaskStateMachine(store)
    status = await sm.get_current_status(UUID(task_id))
    assert status == ev.READY_FOR_IMPLEMENTATION


@pytest.mark.asyncio
async def test_should_fail_add_spec_when_task_id_missing(
    store: InMemoryStore, project_id: object
) -> None:
    parsed = _make_parsed("add_spec", content="## Spec\nDo the thing.")
    result = await execute_action(parsed, store, UUID(str(project_id)))

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_should_fail_add_spec_when_content_missing(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    task = await _seed_task_in_status(store, pid, ev.READY_FOR_SPEC)

    parsed = _make_parsed("add_spec", task_id=str(task.id))  # type: ignore[attr-defined]
    result = await execute_action(parsed, store, pid)

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_should_fail_add_spec_when_task_in_disallowed_status(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    task = await _seed_task_in_status(store, pid, ev.IN_PROGRESS)

    parsed = _make_parsed("add_spec", task_id=str(task.id), content="## Spec")  # type: ignore[attr-defined]
    result = await execute_action(parsed, store, pid)

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_should_fail_add_spec_when_task_belongs_to_different_project(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    other_pid = uuid4()
    task = await _seed_task_in_status(store, other_pid, ev.READY_FOR_SPEC)

    parsed = _make_parsed("add_spec", task_id=str(task.id), content="## Spec")  # type: ignore[attr-defined]
    result = await execute_action(parsed, store, pid)

    assert result.success is False
    assert result.error is not None
    assert "does not belong to this project" in (result.error or "")


@pytest.mark.asyncio
async def test_should_add_spec_from_blocked_status(
    store: InMemoryStore, project_id: object
) -> None:
    pid = UUID(str(project_id))
    task = await _seed_task_in_status(store, pid, ev.BLOCKED)
    task_id = str(task.id)  # type: ignore[attr-defined]

    parsed = _make_parsed("add_spec", task_id=task_id, content="## Revised Spec")
    result = await execute_action(parsed, store, pid)

    assert result.success is True
    sm = TaskStateMachine(store)
    status = await sm.get_current_status(UUID(task_id))
    assert status == ev.READY_FOR_IMPLEMENTATION


@pytest.mark.asyncio
async def test_should_add_spec_from_ready_for_implementation_with_lineage(
    store: InMemoryStore, project_id: object
) -> None:
    from core.spec_manager import SpecManager

    pid = UUID(str(project_id))
    task = await _seed_task_in_status(store, pid, ev.READY_FOR_IMPLEMENTATION)
    task_id = UUID(str(task.id))  # type: ignore[attr-defined]

    # Assign initial spec
    spec_mgr = SpecManager(store)
    first_spec = await spec_mgr.create_spec(task_id, "First spec content")
    await spec_mgr.assign_spec(task_id, first_spec.id)

    # Re-spec via action
    parsed = _make_parsed("add_spec", task_id=str(task_id), content="## Updated Spec")
    result = await execute_action(parsed, store, pid)

    assert result.success is True
    assert result.entity_id is not None

    # Verify lineage — new spec should have previous_spec_id set
    new_spec = await spec_mgr.get_spec(UUID(result.entity_id))
    assert new_spec is not None
    assert new_spec.previous_spec_id == first_spec.id
