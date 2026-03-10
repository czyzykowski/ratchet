"""Unit tests for features route helpers using InMemoryStore (no DB required)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from core import events as ev
from core.feature_manager import FeatureManager
from core.project_manager import ProjectManager
from core.store import InMemoryStore

_PROJECTS_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def store() -> InMemoryStore:
    return InMemoryStore()


async def _create_project(store: InMemoryStore, name: str = "Test Project") -> UUID:
    project_id = uuid4()
    payload = {
        "project_id": str(project_id),
        "name": name,
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
async def test_should_return_empty_groups_when_no_features_exist(
    store: InMemoryStore,
) -> None:
    pm = ProjectManager(store)
    fm = FeatureManager(store)

    project_id = await _create_project(store)

    projects = await pm.list_projects()
    groups = []
    for p in projects:
        features = await fm.list_features(p.id)
        groups.append({"project": p, "features": features})

    assert len(groups) == 1
    assert groups[0]["project"].id == project_id
    assert groups[0]["features"] == []


@pytest.mark.asyncio
async def test_should_count_compiled_and_total_hls_correctly(
    store: InMemoryStore,
) -> None:
    fm = FeatureManager(store)

    project_id = await _create_project(store, "P")
    feature = await fm.create_feature(
        project_id=project_id,
        title="My Feature",
        description="desc",
    )

    hls1 = await fm.add_high_level_spec(
        feature_id=feature.id,
        title="Spec 1",
        order=1,
        content="content 1",
        dependencies=[],
    )
    hls2 = await fm.add_high_level_spec(
        feature_id=feature.id,
        title="Spec 2",
        order=2,
        content="content 2",
        dependencies=[],
    )
    await fm.add_high_level_spec(
        feature_id=feature.id,
        title="Spec 3",
        order=3,
        content="content 3",
        dependencies=[],
    )

    await fm.mark_compiled(hls1.id, uuid4(), feature.id)
    await fm.mark_compiled(hls2.id, uuid4(), feature.id)

    specs = await fm.get_high_level_specs(feature.id)
    compiled_count = sum(1 for s in specs if s.compiled)
    total_count = len(specs)

    assert compiled_count == 2
    assert total_count == 3


@pytest.mark.asyncio
async def test_should_return_none_for_missing_feature_in_detail_view(
    store: InMemoryStore,
) -> None:
    fm = FeatureManager(store)
    feature = await fm.get_feature(uuid4())
    assert feature is None


@pytest.mark.asyncio
async def test_should_order_hls_by_order_field(
    store: InMemoryStore,
) -> None:
    fm = FeatureManager(store)

    project_id = await _create_project(store, "P")
    feature = await fm.create_feature(
        project_id=project_id,
        title="F",
        description="d",
    )

    await fm.add_high_level_spec(feature_id=feature.id, title="Third", order=3, content="c", dependencies=[])
    await fm.add_high_level_spec(feature_id=feature.id, title="First", order=1, content="c", dependencies=[])
    await fm.add_high_level_spec(feature_id=feature.id, title="Second", order=2, content="c", dependencies=[])

    specs = await fm.get_high_level_specs(feature.id)
    orders = [s.order for s in specs]
    assert orders == [1, 2, 3]
