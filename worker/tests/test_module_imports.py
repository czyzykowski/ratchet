"""Smoke tests: verify new module structure imports cleanly."""


def test_worktree_imports() -> None:
    from worker.worktree import (  # noqa: F401
        QAWorktreeError,
        create_baseline_worktree,
        create_qa_worktree,
        find_existing_worktree,
        remove_qa_worktree,
        safe_symlink,
    )


def test_event_helpers_imports() -> None:
    from worker.event_helpers import (  # noqa: F401
        apply_execution_outcome,
        get_qa_fix_attempts,
        gh_command,
        has_pending_baseline_qa_failure,
        should_skip_baseline_qa,
    )


def test_task_finder_imports() -> None:
    from worker.task_finder import TaskFinder  # noqa: F401


def test_pipeline_imports() -> None:
    from worker.pipelines.impl import ImplPipeline  # noqa: F401
    from worker.pipelines.merge import MergePipeline  # noqa: F401
    from worker.pipelines.qa import QAPipeline  # noqa: F401


def test_backwards_compat_dispatcher_imports() -> None:
    from worker.dispatcher import (  # noqa: F401
        DispatchResult,
        ProjectDispatcher,
        QAWorktreeError,
    )


def test_backwards_compat_runner_imports() -> None:
    from worker.runner import (  # noqa: F401
        ProjectDispatcher,
        QAWorktreeError,
        _create_baseline_worktree,
        _create_qa_worktree,
        _remove_qa_worktree,
    )
