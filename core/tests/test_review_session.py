from __future__ import annotations

from datetime import UTC, datetime
from subprocess import CompletedProcess
from unittest.mock import patch
from uuid import UUID, uuid4

from core.models import ReviewRun, ReviewScope, Suggestion, SuggestionEvidence
from core.review_manager import ReviewManager
from core.review_session import ReviewSession
from core.store import InMemoryStore


def make_suggestion(
    review_run_id: UUID,
    order: int = 1,
    status: str = "pending",
) -> Suggestion:
    return Suggestion(
        id=uuid4(),
        review_run_id=review_run_id,
        order=order,
        target="global_claude_md",
        target_path="/some/dir/file.md",
        title=f"Suggestion {order}",
        reasoning="Some reasoning text",
        evidence=SuggestionEvidence(
            task_ids=[uuid4()],
            execution_ids=[uuid4()],
            trace_excerpts=["trace line 1", "trace line 2"],
            failure_reasons=["reason A"],
            spec_refinement_counts={"task1": 2},
            git_log_excerpts=["git log line"],
        ),
        confidence="medium",
        priority="high",
        current_content_excerpt="old content",
        suggested_diff="--- a/file\n+++ b/file\n+new line\n",
        status=status,  # type: ignore[arg-type]
    )


def make_review_run(store: InMemoryStore | None = None) -> tuple[ReviewRun, ReviewManager]:
    if store is None:
        store = InMemoryStore()
    manager = ReviewManager(store)
    run_id = uuid4()
    run = ReviewRun(
        id=run_id,
        scope=ReviewScope(project_ids=[], include_global=True),
        started_at=datetime.now(tz=UTC),
        completed_at=None,
        suggestion_count=0,
        applied_count=0,
        dismissed_count=0,
        previous_run_id=None,
    )
    return run, manager


async def test_should_apply_suggestion_and_update_status_when_user_inputs_a() -> None:
    run, manager = make_review_run()
    suggestion = make_suggestion(run.id)
    session = ReviewSession(manager)

    with patch.object(session, "_read_char", return_value="a"), patch(
        "subprocess.run",
        return_value=CompletedProcess(args=["patch", "-p0"], returncode=0, stdout="", stderr=""),
    ):
        result = await session.run(run, [suggestion])

    assert suggestion.status == "applied"
    assert result[0].status == "applied"


async def test_should_dismiss_suggestion_and_update_status_when_user_inputs_d() -> None:
    run, manager = make_review_run()
    suggestion = make_suggestion(run.id)
    session = ReviewSession(manager)

    with patch.object(session, "_read_char", return_value="d"):
        result = await session.run(run, [suggestion])

    assert suggestion.status == "dismissed"
    assert result[0].status == "dismissed"


async def test_should_skip_suggestion_and_update_status_when_user_inputs_s() -> None:
    run, manager = make_review_run()
    suggestion = make_suggestion(run.id)
    session = ReviewSession(manager)

    with patch.object(session, "_read_char", return_value="s"):
        result = await session.run(run, [suggestion])

    assert suggestion.status == "skipped"
    assert result[0].status == "skipped"


async def test_should_mark_remaining_suggestions_as_skipped_on_quit() -> None:
    run, manager = make_review_run()
    s1 = make_suggestion(run.id, order=1)
    s2 = make_suggestion(run.id, order=2)
    s3 = make_suggestion(run.id, order=3)
    session = ReviewSession(manager)

    chars = iter(["s", "q"])
    with patch.object(session, "_read_char", side_effect=lambda: next(chars)):
        result = await session.run(run, [s1, s2, s3])

    assert result[0].status == "skipped"
    assert result[1].status == "skipped"
    assert result[2].status == "skipped"


async def test_should_re_prompt_after_viewing_full_evidence() -> None:
    run, manager = make_review_run()
    suggestion = make_suggestion(run.id)
    session = ReviewSession(manager)

    chars = iter(["v", "s"])
    with patch.object(session, "_read_char", side_effect=lambda: next(chars)):
        result = await session.run(run, [suggestion])

    assert suggestion.status == "skipped"
    assert result[0].status == "skipped"


async def test_should_handle_patch_failure_gracefully_and_re_prompt() -> None:
    run, manager = make_review_run()
    suggestion = make_suggestion(run.id)
    session = ReviewSession(manager)

    chars = iter(["a", "d"])
    fail_result = CompletedProcess(
        args=["patch", "-p0"], returncode=1, stdout="", stderr="patch failed"
    )

    with patch.object(session, "_read_char", side_effect=lambda: next(chars)), patch(
        "subprocess.run", return_value=fail_result
    ):
        result = await session.run(run, [suggestion])

    assert suggestion.status == "dismissed"
    assert result[0].status == "dismissed"
