"""Unit tests for core/compiler.py using InMemoryStore — no database required."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from core import events as ev
from core.compiler import compile_all, extract_spec, is_eligible
from core.feature_manager import FeatureManager
from core.store import InMemoryStore


def _make_store() -> InMemoryStore:
    return InMemoryStore()


# ---------------------------------------------------------------------------
# extract_spec
# ---------------------------------------------------------------------------


def test_extract_spec_returns_content_after_marker() -> None:
    """should return spec content after ## SPEC READY marker"""
    output = "some preamble\n## SPEC READY\n# Spec 1: My Spec\n\ncontent here"
    result = extract_spec(output)
    assert result == "# Spec 1: My Spec\n\ncontent here"


def test_extract_spec_returns_empty_string_when_marker_missing() -> None:
    """should return empty string when ## SPEC READY marker is not present"""
    output = "no marker here at all"
    result = extract_spec(output)
    assert result == ""


def test_extract_spec_strips_leading_whitespace_after_marker() -> None:
    """should strip leading whitespace from spec content"""
    output = "## SPEC READY\n\n\n# Spec"
    result = extract_spec(output)
    assert result == "# Spec"


# ---------------------------------------------------------------------------
# is_eligible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_eligible_uncompiled_no_deps_returns_true() -> None:
    """should return True for uncompiled HLS with no dependencies"""
    store = _make_store()
    fm = FeatureManager(store)
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    result = await is_eligible(hls, fm, store)
    assert result is True


@pytest.mark.asyncio
async def test_is_eligible_compiled_returns_false() -> None:
    """should return False for already compiled HLS"""
    store = _make_store()
    fm = FeatureManager(store)
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    task_id = uuid.uuid4()
    await fm.mark_compiled(hls.id, task_id, feature.id)
    # Reload so compiled=True is reflected
    specs = await fm.get_high_level_specs(feature.id)
    compiled_hls = specs[0]
    result = await is_eligible(compiled_hls, fm, store)
    assert result is False


@pytest.mark.asyncio
async def test_is_eligible_deps_not_deployed_returns_false() -> None:
    """should return False when dependency task exists but is not deployed"""
    store = _make_store()
    fm = FeatureManager(store)
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls1 = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content 1", [])
    hls2 = await fm.add_high_level_spec(feature.id, "Spec 2", 2, "content 2", [hls1.id])

    # Create a task for hls1 in ready_for_implementation (not deployed)
    dep_task_id = uuid.uuid4()
    await store.append_event(
        aggregate_id=dep_task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(dep_task_id),
            "project_id": str(project_id),
            "title": "Dep task",
            "status": ev.READY_FOR_SPEC,
        },
    )
    await store.append_event(
        aggregate_id=dep_task_id,
        aggregate_type="task",
        event_type=ev.TASK_STATUS_CHANGED,
        payload={"from_status": ev.READY_FOR_SPEC, "to_status": ev.READY_FOR_IMPLEMENTATION},
    )
    await fm.mark_compiled(hls1.id, dep_task_id, feature.id)

    # Load updated hls2 (still uncompiled but dependency not deployed)
    specs = await fm.get_high_level_specs(feature.id)
    hls2_updated = next(s for s in specs if s.id == hls2.id)
    result = await is_eligible(hls2_updated, fm, store)
    assert result is False


@pytest.mark.asyncio
async def test_is_eligible_deps_deployed_returns_true() -> None:
    """should return True when all dependency tasks are deployed"""
    store = _make_store()
    fm = FeatureManager(store)
    project_id = uuid.uuid4()
    feature = await fm.create_feature(project_id, "Feature", "desc")
    hls1 = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content 1", [])
    hls2 = await fm.add_high_level_spec(feature.id, "Spec 2", 2, "content 2", [hls1.id])

    # Create a deployed task for hls1
    dep_task_id = uuid.uuid4()
    await store.append_event(
        aggregate_id=dep_task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(dep_task_id),
            "project_id": str(project_id),
            "title": "Dep task",
            "status": ev.READY_FOR_SPEC,
        },
    )
    await store.append_event(
        aggregate_id=dep_task_id,
        aggregate_type="task",
        event_type=ev.TASK_STATUS_CHANGED,
        payload={"from_status": ev.READY_FOR_SPEC, "to_status": ev.DEPLOYED},
    )
    await fm.mark_compiled(hls1.id, dep_task_id, feature.id)

    # Load updated hls2
    specs = await fm.get_high_level_specs(feature.id)
    hls2_updated = next(s for s in specs if s.id == hls2.id)
    result = await is_eligible(hls2_updated, fm, store)
    assert result is True


# ---------------------------------------------------------------------------
# compile_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_all_iterates_all_projects_and_features() -> None:
    """should call compile_hls for each eligible HLS across projects and features"""
    store = _make_store()
    fm = FeatureManager(store)

    from core.project_manager import ProjectManager

    pm = ProjectManager(store)

    # Register two projects (we mock validate_repo and INTENT.md reading)
    with patch("core.project_manager.validate_repo"):
        project1 = await pm.register_project("P1", "http://p1", "/fake/p1")
        project2 = await pm.register_project("P2", "http://p2", "/fake/p2")

    feature1 = await fm.create_feature(project1.id, "Feature 1", "desc")
    feature2 = await fm.create_feature(project2.id, "Feature 2", "desc")

    hls1 = await fm.add_high_level_spec(feature1.id, "Spec A", 1, "content A", [])
    hls2 = await fm.add_high_level_spec(feature2.id, "Spec B", 1, "content B", [])

    compiled_ids: list[uuid.UUID] = []

    async def mock_compile_hls(**kwargs: object) -> bool:
        hls = kwargs["hls"]
        compiled_ids.append(hls.id)  # type: ignore[arg-type]
        # Actually mark as compiled so is_eligible returns False on retry
        actual_fm = kwargs["fm"]
        task_id = uuid.uuid4()
        await actual_fm.mark_compiled(hls.id, task_id, hls.feature_id)  # type: ignore[union-attr]
        return True

    with (
        patch("core.compiler.compile_hls", side_effect=mock_compile_hls),
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value="intent content"),
    ):
        count = await compile_all(store)

    assert count == 2
    assert hls1.id in compiled_ids
    assert hls2.id in compiled_ids


@pytest.mark.asyncio
async def test_compile_all_skips_project_without_intent_md() -> None:
    """should skip project and log warning when INTENT.md is missing"""
    store = _make_store()

    from core.project_manager import ProjectManager

    pm = ProjectManager(store)
    fm = FeatureManager(store)

    with patch("core.project_manager.validate_repo"):
        project = await pm.register_project("P1", "http://p1", "/fake/p1")

    await fm.create_feature(project.id, "Feature", "desc")

    with patch("pathlib.Path.exists", return_value=False):
        count = await compile_all(store)

    assert count == 0


@pytest.mark.asyncio
async def test_compile_all_returns_zero_when_no_eligible() -> None:
    """should return 0 when all HLS are already compiled"""
    store = _make_store()
    fm = FeatureManager(store)

    from core.project_manager import ProjectManager

    pm = ProjectManager(store)

    with patch("core.project_manager.validate_repo"):
        project = await pm.register_project("P1", "http://p1", "/fake/p1")

    feature = await fm.create_feature(project.id, "Feature", "desc")
    hls = await fm.add_high_level_spec(feature.id, "Spec 1", 1, "content", [])
    task_id = uuid.uuid4()
    await fm.mark_compiled(hls.id, task_id, feature.id)

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value="intent"),
    ):
        count = await compile_all(store)

    assert count == 0
