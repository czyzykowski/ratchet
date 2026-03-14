from __future__ import annotations

from uuid import UUID, uuid4

from core import events as ev
from core.models import ReviewScope, Suggestion, SuggestionEvidence
from core.review_manager import ReviewManager
from core.store import InMemoryStore


def _make_suggestion(
    review_run_id: UUID,
    order: int = 1,
    status: str = "pending",
) -> Suggestion:
    return Suggestion(
        id=uuid4(),
        review_run_id=review_run_id,
        order=order,
        target="global_claude_md",
        target_path="/path/to/file",
        title=f"Suggestion {order}",
        reasoning="Some reasoning",
        evidence=SuggestionEvidence(),
        confidence="medium",
        priority="medium",
        current_content_excerpt="old content",
        suggested_diff="+ new line",
        status=status,  # type: ignore[arg-type]
    )


async def test_should_start_run_and_set_previous_run_id_when_prior_run_exists_with_overlapping_scope(  # noqa: E501
) -> None:
    store = InMemoryStore()
    manager = ReviewManager(store)
    pid = uuid4()
    scope = ReviewScope(project_ids=[pid], include_global=False)
    first_run = await manager.start_run(scope)
    second_run = await manager.start_run(scope)
    assert second_run.previous_run_id == first_run.id


async def test_should_not_set_previous_run_id_when_no_prior_run_exists() -> None:
    store = InMemoryStore()
    manager = ReviewManager(store)
    scope = ReviewScope(project_ids=[uuid4()], include_global=False)
    run = await manager.start_run(scope)
    assert run.previous_run_id is None


async def test_should_record_suggestions_with_sequential_order() -> None:
    store = InMemoryStore()
    manager = ReviewManager(store)
    scope = ReviewScope(project_ids=[], include_global=True)
    run = await manager.start_run(scope)

    for i in range(1, 4):
        suggestion = _make_suggestion(run.id, order=i)
        await manager.record_suggestion(suggestion)

    run_events = await store.get_events(run.id, "review_run")
    created_events = [
        e for e in run_events if e.event_type == ev.REVIEW_SUGGESTION_CREATED
    ]
    assert len(created_events) == 3
    orders = [e.payload["order"] for e in created_events]
    assert orders == [1, 2, 3]


async def test_should_complete_run_with_correct_applied_and_dismissed_counts() -> None:
    store = InMemoryStore()
    manager = ReviewManager(store)
    scope = ReviewScope(project_ids=[], include_global=True)
    run = await manager.start_run(scope)

    suggestions = [
        _make_suggestion(run.id, order=1, status="applied"),
        _make_suggestion(run.id, order=2, status="applied"),
        _make_suggestion(run.id, order=3, status="dismissed"),
        _make_suggestion(run.id, order=4, status="pending"),
    ]
    result = await manager.complete_run(run.id, suggestions)

    assert result.applied_count == 2
    assert result.dismissed_count == 1
    assert result.suggestion_count == 4


async def test_should_list_runs_sorted_by_started_at_descending() -> None:
    store = InMemoryStore()
    manager = ReviewManager(store)
    scope = ReviewScope(project_ids=[], include_global=True)

    run1 = await manager.start_run(scope)
    run2 = await manager.start_run(scope)
    run3 = await manager.start_run(scope)

    runs = await manager.list_runs()
    assert len(runs) >= 3
    started_ats = [r.started_at for r in runs[:3]]
    assert started_ats == sorted(started_ats, reverse=True)
    run_ids = [r.id for r in runs]
    assert run1.id in run_ids
    assert run2.id in run_ids
    assert run3.id in run_ids


async def test_should_generate_previous_run_summary_string_containing_suggestion_titles_and_statuses(  # noqa: E501
) -> None:
    store = InMemoryStore()
    manager = ReviewManager(store)
    scope = ReviewScope(project_ids=[], include_global=True)
    run = await manager.start_run(scope)

    s1 = _make_suggestion(run.id, order=1)
    s1 = s1.model_copy(update={"title": "Improve logging"})
    s2 = _make_suggestion(run.id, order=2)
    s2 = s2.model_copy(update={"title": "Fix retry logic"})

    await manager.record_suggestion(s1)
    await manager.record_suggestion(s2)
    await manager.apply_suggestion(run.id, s1.id, s1.target_path)
    await manager.dismiss_suggestion(run.id, s2.id)

    summary = await manager.get_previous_run_summary(run.id)
    assert "Improve logging" in summary
    assert "Fix retry logic" in summary
    assert "applied" in summary
    assert "dismissed" in summary


async def test_should_return_none_from_get_run_for_unknown_id() -> None:
    store = InMemoryStore()
    manager = ReviewManager(store)
    result = await manager.get_run(uuid4())
    assert result is None
