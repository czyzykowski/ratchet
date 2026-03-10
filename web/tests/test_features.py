"""Unit tests for features routes using InMemoryStore (no DB required)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from core import events as ev
from core.feature_manager import FeatureManager
from core.project_manager import ProjectManager
from core.store import InMemoryStore

_PROJECTS_REGISTRY_ID = __import__("uuid").UUID("00000000-0000-0000-0000-000000000001")


async def _make_project(store: InMemoryStore) -> __import__("uuid").UUID:
    project_id = uuid4()
    payload = {
        "project_id": str(project_id),
        "name": "Test Project",
        "repo_url": "https://example.com/repo.git",
        "local_path": "/tmp/test",
        "status": "active",
    }
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=_PROJECTS_REGISTRY_ID,
        aggregate_type="projects",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )
    return project_id


@pytest.mark.asyncio
async def test_should_return_empty_groups_when_no_features_exist() -> None:
    store = InMemoryStore()
    project_id = await _make_project(store)
    pm = ProjectManager(store)
    fm = FeatureManager(store)

    projects = await pm.list_projects()
    groups = []
    for project in projects:
        raw_features = await fm.list_features(project.id)
        feature_dicts = []
        for feature in raw_features:
            specs = await fm.get_high_level_specs(feature.id)
            compiled_count = sum(1 for s in specs if s.compiled)
            feature_dicts.append(
                {
                    "feature": feature,
                    "compiled_count": compiled_count,
                    "total_count": len(specs),
                }
            )
        groups.append({"project": project, "features": feature_dicts})

    assert len(groups) == 1
    assert groups[0]["project"].id == project_id
    assert groups[0]["features"] == []


@pytest.mark.asyncio
async def test_should_count_compiled_and_total_hls_correctly() -> None:
    store = InMemoryStore()
    project_id = await _make_project(store)
    fm = FeatureManager(store)

    feature = await fm.create_feature(
        project_id=project_id,
        title="My Feature",
        description="desc",
    )

    hls1 = await fm.add_high_level_spec(
        feature_id=feature.id, title="Spec A", order=1, content="a", dependencies=[]
    )
    hls2 = await fm.add_high_level_spec(
        feature_id=feature.id, title="Spec B", order=2, content="b", dependencies=[]
    )
    await fm.add_high_level_spec(
        feature_id=feature.id, title="Spec C", order=3, content="c", dependencies=[]
    )

    await fm.mark_compiled(hls_id=hls1.id, task_id=uuid4(), feature_id=feature.id)
    await fm.mark_compiled(hls_id=hls2.id, task_id=uuid4(), feature_id=feature.id)

    specs = await fm.get_high_level_specs(feature.id)
    compiled_count = sum(1 for s in specs if s.compiled)

    assert compiled_count == 2
    assert len(specs) == 3


@pytest.mark.asyncio
async def test_should_return_none_for_missing_feature_in_detail_view() -> None:
    store = InMemoryStore()
    fm = FeatureManager(store)

    missing_id = uuid4()
    feature = await fm.get_feature(missing_id)
    assert feature is None


@pytest.mark.asyncio
async def test_should_order_hls_by_order_field() -> None:
    store = InMemoryStore()
    project_id = await _make_project(store)
    fm = FeatureManager(store)

    feature = await fm.create_feature(
        project_id=project_id,
        title="Ordered Feature",
        description="desc",
    )

    await fm.add_high_level_spec(
        feature_id=feature.id, title="Third", order=3,
        content="c", dependencies=[],
    )
    await fm.add_high_level_spec(
        feature_id=feature.id, title="First", order=1,
        content="c", dependencies=[],
    )
    await fm.add_high_level_spec(
        feature_id=feature.id, title="Second", order=2,
        content="c", dependencies=[],
    )

    specs = await fm.get_high_level_specs(feature.id)

    assert [s.order for s in specs] == [1, 2, 3]
    assert [s.title for s in specs] == ["First", "Second", "Third"]
