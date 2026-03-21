"""Stateless event query helpers for dispatcher pipelines."""

from __future__ import annotations

import subprocess as _subprocess
from typing import Any

from core import events as ev
from core.state_machine import TaskStateMachine
from core.task_executor import ExecutionOutcome, ExecutionResult


def has_pending_baseline_qa_failure(task_events: list[Any]) -> bool:
    """True if the most recent baseline QA failure has no retry or force-execute after it."""
    last_failed_seq: int | None = None
    last_cleared_seq: int | None = None
    for event in task_events:
        if event.event_type == ev.TASK_BASELINE_QA_FAILED:
            last_failed_seq = event.sequence
        elif event.event_type in (ev.TASK_BASELINE_QA_RETRY, ev.TASK_FORCE_EXECUTE):
            last_cleared_seq = max(last_cleared_seq or 0, event.sequence)
    if last_failed_seq is None:
        return False
    return last_cleared_seq is None or last_failed_seq > last_cleared_seq


def should_skip_baseline_qa(task_events: list[Any]) -> bool:
    """True if force-execute (not mere retry) was requested after the last baseline QA failure."""
    last_failed_seq: int | None = None
    last_force_seq: int | None = None
    for event in task_events:
        if event.event_type == ev.TASK_BASELINE_QA_FAILED:
            last_failed_seq = event.sequence
        elif event.event_type == ev.TASK_FORCE_EXECUTE:
            last_force_seq = event.sequence
    if last_force_seq is None:
        return False
    return last_failed_seq is None or last_force_seq > last_failed_seq


async def apply_execution_outcome(
    state_machine: TaskStateMachine,
    task: Any,
    result: ExecutionResult,
) -> None:
    """Translate an ExecutionResult into task state transitions."""
    if result.outcome == ExecutionOutcome.COMPLETED:
        await state_machine.transition(task.id, ev.READY_FOR_QA)
    else:
        failure_reason = result.failure_reason or result.outcome.value
        await state_machine.transition(
            task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
        )


def gh_command(args: list[str], project_local_path: str) -> _subprocess.CompletedProcess[str]:
    """Run a gh command, wrapped in nix develop if flake.nix is present."""
    from pathlib import Path

    if (Path(project_local_path) / "flake.nix").exists():
        return _subprocess.run(
            ["nix", "develop", "--command", "gh"] + args,
            cwd=project_local_path,
            capture_output=True,
            text=True,
        )
    return _subprocess.run(
        ["gh"] + args,
        cwd=project_local_path,
        capture_output=True,
        text=True,
    )


def get_qa_fix_attempts(task_events: list[Any]) -> int:
    """Read qa_fix_attempts from the latest TASK_STATUS_CHANGED event payload (default 0)."""
    attempts = 0
    for event in reversed(task_events):
        if event.event_type == ev.TASK_STATUS_CHANGED:
            val = event.payload.get("qa_fix_attempts")
            if val is not None:
                attempts = int(val)
                break
    return attempts
