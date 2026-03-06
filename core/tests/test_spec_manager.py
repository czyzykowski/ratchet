"""Unit tests for SpecManager using InMemoryStore — no database required."""

from __future__ import annotations

import uuid

from core import events as ev
from core.spec_manager import SpecManager
from core.store import InMemoryStore


def _make_sm() -> tuple[SpecManager, InMemoryStore]:
    store = InMemoryStore()
    sm = SpecManager(store)
    return sm, store


# ---------------------------------------------------------------------------
# Lineage chain
# ---------------------------------------------------------------------------


async def test_create_first_spec_lineage_length_one() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    await sm.create_spec(task_id, "first spec content")
    lineage = await sm.get_spec_lineage(task_id)
    assert len(lineage) == 1
    assert lineage[0].content == "first spec content"
    assert lineage[0].previous_spec_id is None


async def test_create_second_spec_linked_to_first_lineage_length_two() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    spec_a = await sm.create_spec(task_id, "spec A")
    spec_b = await sm.create_spec(task_id, "spec B", previous_spec_id=spec_a.id)
    lineage = await sm.get_spec_lineage(task_id)
    assert len(lineage) == 2
    assert lineage[0].id == spec_a.id
    assert lineage[1].id == spec_b.id


async def test_create_third_spec_linked_lineage_length_three_oldest_first() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    spec_a = await sm.create_spec(task_id, "spec A")
    spec_b = await sm.create_spec(task_id, "spec B", previous_spec_id=spec_a.id)
    spec_c = await sm.create_spec(task_id, "spec C", previous_spec_id=spec_b.id)
    lineage = await sm.get_spec_lineage(task_id)
    assert len(lineage) == 3
    assert lineage[0].id == spec_a.id
    assert lineage[1].id == spec_b.id
    assert lineage[2].id == spec_c.id


async def test_get_spec_lineage_empty_when_no_specs() -> None:
    sm, _ = _make_sm()
    lineage = await sm.get_spec_lineage(uuid.uuid4())
    assert lineage == []


async def test_get_spec_lineage_correct_order_regardless_of_event_insertion_order() -> None:
    """Lineage order is determined by previous_spec_id chain, not event sequence."""
    sm, store = _make_sm()
    task_id = uuid.uuid4()
    # Create spec_a first (no previous)
    spec_a = await sm.create_spec(task_id, "spec A")
    # Create spec_b linked to spec_a
    spec_b = await sm.create_spec(task_id, "spec B", previous_spec_id=spec_a.id)
    # Create spec_c linked to spec_b
    spec_c = await sm.create_spec(task_id, "spec C", previous_spec_id=spec_b.id)
    lineage = await sm.get_spec_lineage(task_id)
    # Regardless of store order, chain should be A → B → C oldest first
    assert [s.id for s in lineage] == [spec_a.id, spec_b.id, spec_c.id]


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


async def test_get_current_spec_before_assignment_returns_none() -> None:
    sm, _ = _make_sm()
    result = await sm.get_current_spec(uuid.uuid4())
    assert result is None


async def test_assign_spec_then_get_current_spec_returns_correct_spec() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    spec = await sm.create_spec(task_id, "my spec")
    await sm.assign_spec(task_id, spec.id)
    current = await sm.get_current_spec(task_id)
    assert current is not None
    assert current.id == spec.id
    assert current.content == "my spec"


async def test_assign_second_spec_get_current_returns_second() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    spec_a = await sm.create_spec(task_id, "spec A")
    spec_b = await sm.create_spec(task_id, "spec B", previous_spec_id=spec_a.id)
    await sm.assign_spec(task_id, spec_a.id)
    await sm.assign_spec(task_id, spec_b.id)
    current = await sm.get_current_spec(task_id)
    assert current is not None
    assert current.id == spec_b.id


async def test_assign_spec_event_payload_has_correct_previous_spec_id() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    spec_a = await sm.create_spec(task_id, "spec A")
    spec_b = await sm.create_spec(task_id, "spec B", previous_spec_id=spec_a.id)
    event_a = await sm.assign_spec(task_id, spec_a.id)
    assert event_a.event_type == ev.TASK_SPEC_ASSIGNED
    assert event_a.payload["spec_id"] == str(spec_a.id)
    assert event_a.payload["previous_spec_id"] is None
    event_b = await sm.assign_spec(task_id, spec_b.id)
    assert event_b.payload["spec_id"] == str(spec_b.id)
    assert event_b.payload["previous_spec_id"] == str(spec_a.id)


# ---------------------------------------------------------------------------
# get_spec
# ---------------------------------------------------------------------------


async def test_get_spec_returns_correct_spec() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    created = await sm.create_spec(task_id, "some content")
    found = await sm.get_spec(created.id)
    assert found is not None
    assert found.id == created.id
    assert found.task_id == task_id
    assert found.content == "some content"
    assert found.previous_spec_id is None


async def test_get_spec_unknown_id_returns_none() -> None:
    sm, _ = _make_sm()
    result = await sm.get_spec(uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# create_spec does not automatically assign
# ---------------------------------------------------------------------------


async def test_create_spec_does_not_assign() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    await sm.create_spec(task_id, "spec content")
    current = await sm.get_current_spec(task_id)
    assert current is None


# ---------------------------------------------------------------------------
# Full workflow
# ---------------------------------------------------------------------------


async def test_full_workflow_create_assign_create_assign() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    spec_a = await sm.create_spec(task_id, "spec A")
    await sm.assign_spec(task_id, spec_a.id)
    spec_b = await sm.create_spec(task_id, "spec B", previous_spec_id=spec_a.id)
    await sm.assign_spec(task_id, spec_b.id)
    current = await sm.get_current_spec(task_id)
    assert current is not None
    assert current.id == spec_b.id
    lineage = await sm.get_spec_lineage(task_id)
    assert len(lineage) == 2
    assert lineage[0].id == spec_a.id
    assert lineage[1].id == spec_b.id


async def test_refinement_count_derivable_from_lineage_length() -> None:
    sm, _ = _make_sm()
    task_id = uuid.uuid4()
    spec_a = await sm.create_spec(task_id, "spec A")
    assert len(await sm.get_spec_lineage(task_id)) == 1
    spec_b = await sm.create_spec(task_id, "spec B", previous_spec_id=spec_a.id)
    assert len(await sm.get_spec_lineage(task_id)) == 2
    await sm.create_spec(task_id, "spec C", previous_spec_id=spec_b.id)
    assert len(await sm.get_spec_lineage(task_id)) == 3
