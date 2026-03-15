"""Unit tests for FeatureManager using InMemoryStore — no database required."""

from __future__ import annotations

import uuid

import pytest

from core import events as ev
from core.feature_manager import FeatureManager
from core.store import InMemoryStore


def _make_fm() -> tuple[FeatureManager, InMemoryStore]:
    store = InMemoryStore()
    fm = FeatureManager(store)
    return fm, store


# ---------------------------------------------------------------------------
# create_feature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_feature_returns_correct_fields() -> None:
    """should return Feature with correct fields when created"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "My Feature", "A description")
    assert feature.project_id == project_id
    assert feature.title == "My Feature"
    assert feature.description == "A description"
    assert feature.id is not None
    assert feature.created_at is not None
    assert feature.updated_at is not None


@pytest.mark.asyncio
async def test_create_feature_appends_event() -> None:
    """should append FEATURE_CREATED event to store"""
    fm, store = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature X", "desc")
    feature_events = await store.get_events(feature.id, "feature")
    assert len(feature_events) == 1
    assert feature_events[0].event_type == ev.FEATURE_CREATED


@pytest.mark.asyncio
async def test_create_feature_dual_writes_to_project_registry() -> None:
    """should dual-write to project_features registry"""
    fm, store = _make_fm()
    project_id = uuid.uuid4()
    await fm.create_feature(project_id, "Feature X", "desc")
    registry_events = await store.get_events(project_id, "project_features")
    assert len(registry_events) == 1
    assert registry_events[0].event_type == ev.FEATURE_CREATED


# ---------------------------------------------------------------------------
# get_feature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_feature_returns_correct_feature() -> None:
    """should return Feature with correct id and title"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    created = await fm.create_feature(project_id, "Test", "desc")
    found = await fm.get_feature(created.id)
    assert found is not None
    assert found.id == created.id
    assert found.title == "Test"


@pytest.mark.asyncio
async def test_get_feature_unknown_id_returns_none() -> None:
    """should return None for unknown feature_id"""
    fm, _ = _make_fm()
    result = await fm.get_feature(uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# list_features
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_features_empty_when_none_created() -> None:
    """should return empty list when no features exist for project"""
    fm, _ = _make_fm()
    result = await fm.list_features(uuid.uuid4())
    assert result == []


@pytest.mark.asyncio
async def test_list_features_returns_features_for_project() -> None:
    """should return features ordered by created_at ascending"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    f1 = await fm.create_feature(project_id, "Feature 1", "desc 1")
    f2 = await fm.create_feature(project_id, "Feature 2", "desc 2")
    result = await fm.list_features(project_id)
    assert len(result) == 2
    assert result[0].id == f1.id
    assert result[1].id == f2.id


@pytest.mark.asyncio
async def test_list_features_only_returns_features_for_requested_project() -> None:
    """should not return features from other projects"""
    fm, _ = _make_fm()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    await fm.create_feature(project_a, "Feature A", "desc")
    await fm.create_feature(project_b, "Feature B", "desc")
    result = await fm.list_features(project_a)
    assert len(result) == 1
    assert result[0].title == "Feature A"


# ---------------------------------------------------------------------------
# session_id round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_store_session_id_in_event_payload_when_provided() -> None:
    """should store session_id in event payload when provided"""
    fm, store = _make_fm()
    project_id = uuid.uuid4()
    session_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "F", "desc", session_id=session_id)
    assert feature.session_id == session_id
    feature_events = await store.get_events(feature.id, "feature")
    assert feature_events[0].payload["session_id"] == str(session_id)


@pytest.mark.asyncio
async def test_should_return_session_id_on_get_feature_when_stored() -> None:
    """should return session_id on get_feature when stored"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    session_id = uuid.uuid4()
    created = await fm.create_feature(project_id, "F", "desc", session_id=session_id)
    found = await fm.get_feature(created.id)
    assert found is not None
    assert found.session_id == session_id


@pytest.mark.asyncio
async def test_should_return_none_for_session_id_when_not_provided() -> None:
    """should return None for session_id when not provided"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    created = await fm.create_feature(project_id, "F", "desc")
    found = await fm.get_feature(created.id)
    assert found is not None
    assert found.session_id is None


# ---------------------------------------------------------------------------
# add_high_level_spec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_high_level_spec_returns_correct_fields() -> None:
    """should return HighLevelSpec with correct fields"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    assert hls.feature_id == feature.id
    assert hls.title == "Spec 1"
    assert hls.order == 1
    assert hls.content == "content"
    assert hls.compiled is False
    assert hls.task_id is None
    assert hls.dependencies == []


@pytest.mark.asyncio
async def test_add_high_level_spec_with_dependencies() -> None:
    """should store dependencies as list of UUIDs"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    dep1 = uuid.uuid4()
    dep2 = uuid.uuid4()
    hls = await fm.add_high_level_spec(feature.id, "Spec 2", 2, "content", [dep1, dep2])
    assert dep1 in hls.dependencies
    assert dep2 in hls.dependencies


# ---------------------------------------------------------------------------
# get_high_level_specs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_high_level_specs_empty_when_none_added() -> None:
    """should return empty list when no specs added to feature"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    result = await fm.get_high_level_specs(feature.id)
    assert result == []


@pytest.mark.asyncio
async def test_get_high_level_specs_returns_in_order() -> None:
    """should return specs ordered by order field ascending"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls2 = await fm.add_high_level_spec(feature.id, "Spec 2", 2, "content 2", [])
    hls1 = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content 1", [])
    result = await fm.get_high_level_specs(feature.id)
    assert len(result) == 2
    assert result[0].id == hls1.id
    assert result[1].id == hls2.id


# ---------------------------------------------------------------------------
# mark_compiled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_compiled_sets_compiled_true_and_task_id() -> None:
    """should set compiled=True and task_id after mark_compiled"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    task_id = uuid.uuid4()
    await fm.mark_compiled(hls.id, task_id, feature.id)
    specs = await fm.get_high_level_specs(feature.id)
    assert len(specs) == 1
    assert specs[0].compiled is True
    assert specs[0].task_id == task_id


@pytest.mark.asyncio
async def test_mark_compiled_appends_event() -> None:
    """should append HIGH_LEVEL_SPEC_COMPILED event"""
    fm, store = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec", 1, "content", [])
    task_id = uuid.uuid4()
    await fm.mark_compiled(hls.id, task_id, feature.id)
    feature_events = await store.get_events(feature.id, "feature")
    compiled_events = [e for e in feature_events if e.event_type == ev.HIGH_LEVEL_SPEC_COMPILED]
    assert len(compiled_events) == 1
    assert compiled_events[0].payload["task_id"] == str(task_id)


@pytest.mark.asyncio
async def test_mark_compiled_only_affects_targeted_spec() -> None:
    """should only mark the targeted spec as compiled, not others"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls1 = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content 1", [])
    hls2 = await fm.add_high_level_spec(feature.id, "Spec 2", 2, "content 2", [])
    task_id = uuid.uuid4()
    await fm.mark_compiled(hls1.id, task_id, feature.id)
    specs = await fm.get_high_level_specs(feature.id)
    spec1 = next(s for s in specs if s.id == hls1.id)
    spec2 = next(s for s in specs if s.id == hls2.id)
    assert spec1.compiled is True
    assert spec2.compiled is False


# ---------------------------------------------------------------------------
# get_feature_status — derived status logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_feature_status_defined_when_no_specs() -> None:
    """should return defined when feature has no high-level specs"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    status = await fm.get_feature_status(feature.id)
    assert status == ev.FEATURE_DEFINED


@pytest.mark.asyncio
async def test_get_feature_status_defined_when_no_compiled_specs() -> None:
    """should return defined when specs exist but none are compiled"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    status = await fm.get_feature_status(feature.id)
    assert status == ev.FEATURE_DEFINED


def test_feature_status_constants_exist() -> None:
    """should verify all six feature status constants exist with correct string values"""
    assert ev.FEATURE_IDEA == "idea"
    assert ev.FEATURE_IN_CLARIFICATION == "in_clarification"
    assert ev.FEATURE_DEFINED == "defined"
    assert ev.FEATURE_GENERATED == "generated"
    assert ev.FEATURE_IN_PROGRESS == "in_progress"
    assert ev.FEATURE_DONE == "done"


def test_feature_draft_constant_removed() -> None:
    """should verify FEATURE_DRAFT constant no longer exists"""
    assert not hasattr(ev, "FEATURE_DRAFT")


@pytest.mark.asyncio
async def test_get_feature_status_generated_when_tasks_in_early_statuses() -> None:
    """should return generated when all compiled specs have tasks in early statuses"""
    fm, store = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    task_id = uuid.uuid4()
    # Create a task in ready_for_spec status
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"task_id": str(task_id), "project_id": str(project_id), "title": "T",
                 "status": ev.READY_FOR_SPEC},
    )
    await fm.mark_compiled(hls.id, task_id, feature.id)
    status = await fm.get_feature_status(feature.id)
    assert status == ev.FEATURE_GENERATED


@pytest.mark.asyncio
async def test_get_feature_status_in_progress_when_task_advanced() -> None:
    """should return in_progress when ≥1 task is past ready_for_implementation"""
    fm, store = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    task_id = uuid.uuid4()
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"task_id": str(task_id), "project_id": str(project_id), "title": "T",
                 "status": ev.READY_FOR_SPEC},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_STATUS_CHANGED,
        payload={"from_status": ev.READY_FOR_SPEC, "to_status": ev.IN_PROGRESS},
    )
    await fm.mark_compiled(hls.id, task_id, feature.id)
    status = await fm.get_feature_status(feature.id)
    assert status == ev.FEATURE_IN_PROGRESS


@pytest.mark.asyncio
async def test_get_feature_status_done_when_all_tasks_deployed() -> None:
    """should return done when all compiled specs have deployed tasks"""
    fm, store = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    task_id = uuid.uuid4()
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={"task_id": str(task_id), "project_id": str(project_id), "title": "T",
                 "status": ev.READY_FOR_SPEC},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_STATUS_CHANGED,
        payload={"from_status": ev.READY_FOR_SPEC, "to_status": ev.DEPLOYED},
    )
    await fm.mark_compiled(hls.id, task_id, feature.id)
    status = await fm.get_feature_status(feature.id)
    assert status == ev.FEATURE_DONE


# ---------------------------------------------------------------------------
# Dependency eligibility logic (tested via get_high_level_specs + mark_compiled)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_eligibility_no_deps_immediately_eligible() -> None:
    """High-level specs with no dependencies should be immediately eligible"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    specs = await fm.get_high_level_specs(feature.id)
    assert specs[0].compiled is False
    assert specs[0].dependencies == []


@pytest.mark.asyncio
async def test_dependency_eligibility_with_deps_not_compiled() -> None:
    """High-level spec with unresolved dependencies should not be compiled"""
    fm, _ = _make_fm()
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls1 = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content 1", [])
    hls2 = await fm.add_high_level_spec(feature.id, "Spec 2", 2, "content 2", [hls1.id])
    specs = await fm.get_high_level_specs(feature.id)
    spec2 = next(s for s in specs if s.id == hls2.id)
    # hls2 depends on hls1, which is not compiled, so it should not be eligible
    assert spec2.compiled is False
    assert hls1.id in spec2.dependencies
